import time
import json
import shutil
import tempfile
from http.client import HTTPException
from pathlib import Path
from threading import Thread

from requests import RequestException

from core.tiktok_api import TikTokAPI
from utils.logger_manager import logger
from utils.recorder_config import RecorderConfig
from utils.video_management import VideoManagement
from utils.custom_exceptions import LiveNotFound, UserLiveError, TikTokRecorderError
from utils.enums import Mode, Error, TimeOut, TikTokError


class TikTokRecorder:
    def __init__(self, config: RecorderConfig):
        self.tiktok = TikTokAPI(proxy=config.proxy, cookies=config.cookies)

        self.url = config.url
        self.user = config.user
        self.room_id = config.room_id
        self.mode = config.mode
        self.automatic_interval = config.automatic_interval
        self.duration = config.duration
        self.output = config.output
        self.bitrate = config.bitrate
        self.ffmpeg_path = config.ffmpeg_path
        self.use_telegram = config.use_telegram
        from core.output_options import OutputOptions
        self.outputs = OutputOptions.from_legacy(config.panel, config.guests, config.comments, config.layout)
        self.panel = self.outputs.guests or self.outputs.comments
        self._proxy = config.proxy
        self._cookies = config.cookies
        self.stop_event = config.stop_event
        self.event_callback = config.event_callback

    def _stopped(self):
        return self.stop_event is not None and self.stop_event.is_set()

    def _wait(self, seconds):
        if self.stop_event is not None:
            return self.stop_event.wait(seconds)
        time.sleep(seconds)
        return False

    def _state(self, state, message, **details):
        if self.event_callback:
            try:
                self.event_callback({"type": "state", "state": state, "message": message, **details})
            except Exception:
                logger.debug("Recorder status callback failed", exc_info=True)

    def _setup(self):
        """Resolve user/room data and validate prerequisites via network calls."""
        if self.mode == Mode.FOLLOWERS:
            self.check_country_blacklisted()

            self.sec_uid = self.tiktok.get_sec_uid()
            if self.sec_uid is None:
                raise TikTokRecorderError("Failed to retrieve sec_uid.")

            logger.info("Followers mode activated\n")
        else:
            if self.url:
                self.user, self.room_id = self.tiktok.get_room_and_user_from_url(
                    self.url
                )

            if not self.user:
                self.user = self.tiktok.get_user_from_room_id(self.room_id)

            if not self.room_id:
                self.room_id = self.tiktok.get_room_id_from_user(self.user)

            self.check_country_blacklisted()

            logger.info(f"USERNAME: {self.user}" + ("\n" if not self.room_id else ""))
            if self.room_id:
                logger.info(
                    f"ROOM_ID:  {self.room_id}"
                    + ("\n" if not self.tiktok.is_room_alive(self.room_id) else "")
                )

        # If proxy was used for the initial checks, switch to a direct connection
        # for the actual stream download to avoid proxy bottlenecks
        if self._proxy:
            self.tiktok = TikTokAPI(proxy=None, cookies=self._cookies)

    def run(self):
        """
        Resolves prerequisites and runs the recorder in the selected mode.

        If the mode is MANUAL, it checks if the user is currently live and
        if so, starts recording.

        If the mode is AUTOMATIC, it continuously checks if the user is live
        and if not, waits for the specified timeout before rechecking.
        If the user is live, it starts recording.

        if the mode is FOLLOWERS, it continuously checks the followers of
        the authenticated user. If any follower is live, it starts recording
        their live stream in a separate process.
        """
        if self._stopped():
            return
        self._state("connecting", "Connecting to TikTok...")
        self._setup()
        if self._stopped():
            return

        if self.mode == Mode.MANUAL:
            self.manual_mode()

        elif self.mode == Mode.AUTOMATIC:
            self.automatic_mode()

        elif self.mode == Mode.FOLLOWERS:
            self.followers_mode()

    def manual_mode(self):
        if self._stopped():
            return
        if not self.tiktok.is_room_alive(self.room_id):
            raise UserLiveError(f"@{self.user}: {TikTokError.USER_NOT_CURRENTLY_LIVE}")

        self.start_recording(self.user, self.room_id)

    def automatic_mode(self):
        while not self._stopped():
            try:
                self.room_id = self.tiktok.get_room_id_from_user(self.user)
                self.manual_mode()

            except (UserLiveError, LiveNotFound) as ex:
                logger.info(ex)
                logger.info(
                    f"Waiting {self.automatic_interval} minutes before recheck\n"
                )
                self._state("waiting", f"Waiting for @{self.user} to go live...")
                self._wait(self.automatic_interval * TimeOut.ONE_MINUTE)

            except (ConnectionError, RequestException, HTTPException):
                logger.error(Error.CONNECTION_CLOSED_AUTOMATIC)
                self._state("waiting", "Connection interrupted. Waiting before retrying...")
                self._wait(TimeOut.CONNECTION_CLOSED * TimeOut.ONE_MINUTE)

    def followers_mode(self):
        active_recordings = {}  # follower -> Thread

        while not self._stopped():
            try:
                followers = self.tiktok.get_followers_list(self.sec_uid)

                for follower in followers:
                    if self._stopped():
                        break
                    if follower in active_recordings:
                        if not active_recordings[follower].is_alive():
                            logger.info(f"Recording of @{follower} finished.")
                            del active_recordings[follower]
                        else:
                            continue

                    try:
                        room_id = self.tiktok.get_room_id_from_user(follower)

                        if not room_id or not self.tiktok.is_room_alive(room_id):
                            continue

                        logger.info(f"@{follower} is live. Starting recording...")

                        thread = Thread(
                            target=self.start_recording,
                            args=(follower, room_id),
                            daemon=True,
                        )
                        thread.start()
                        active_recordings[follower] = thread

                        self._wait(2.5)

                    except TikTokRecorderError as e:
                        logger.error(f"Error while processing @{follower}: {e}")
                        continue

                    except Exception as e:
                        logger.error(
                            f"Unexpected error processing @{follower}: {e}",
                            exc_info=True,
                        )
                        continue

                print()
                logger.info(
                    f"Waiting {self.automatic_interval} minutes for the next check..."
                )
                self._wait(self.automatic_interval * TimeOut.ONE_MINUTE)

            except (UserLiveError, LiveNotFound) as ex:
                logger.info(ex)
                logger.info(
                    f"Waiting {self.automatic_interval} minutes before recheck\n"
                )
                self._wait(self.automatic_interval * TimeOut.ONE_MINUTE)

            except (ConnectionError, RequestException, HTTPException):
                logger.error(Error.CONNECTION_CLOSED_AUTOMATIC)
                self._wait(TimeOut.CONNECTION_CLOSED * TimeOut.ONE_MINUTE)

    def _build_output_path(self, user: str) -> str:
        filename = (
            f"TK_{user}_{time.strftime('%Y.%m.%d_%H-%M-%S', time.localtime())}_flv.mp4"
        )
        if self.output:
            return str(Path(self.output) / filename)
        return filename

    def start_recording(self, user, room_id):
        """
        Start recording live
        """
        if self._stopped():
            return
        live_urls = self.tiktok.get_live_url_candidates(room_id, user=user)
        if self._stopped():
            return
        if not live_urls:
            raise LiveNotFound(TikTokError.RETRIEVE_LIVE_URL)

        final_path = Path(self._build_output_path(user)).resolve()
        final_path.parent.mkdir(parents=True, exist_ok=True)
        workspace = Path(tempfile.mkdtemp(prefix=".recording-", dir=final_path.parent))
        output = str(workspace / final_path.name)
        panel_capture = None
        if self.panel:
            try:
                from core.guest_panel import GuestPanelCapture

                panel_kwargs = {"event_callback": self.event_callback} if self.event_callback else {}
                panel_capture = GuestPanelCapture(user, room_id, output, self._cookies, self._proxy, **panel_kwargs)
                panel_capture.start()
            except Exception as exc:
                logger.warning("Panel setup failed; saving the original recording: %s", exc)
                if panel_capture:
                    panel_capture.stop()
                panel_capture = None

        try:
            try:
                self._record_live_urls(user, room_id, live_urls, output, panel_capture)
            finally:
                if panel_capture:
                    self._state("finalizing", "Finishing guest and comment capture...", progress=None)
                    panel_capture.stop()
            converted = Path(output.replace("_flv.mp4", ".mp4"))
            if not converted.is_file():
                if self._stopped() and not Path(output).exists():
                    shutil.rmtree(workspace)
                    return
                raise RuntimeError("The captured feed could not be converted to MP4.")
            if self.panel and panel_capture is None:
                raise RuntimeError("Selected panel data could not be collected. The feed is preserved for recovery.")
            from core.recording_export import export_recording
            timeline = panel_capture.timeline.export() if panel_capture else {}
            assets = panel_capture.assets if panel_capture else workspace / "assets"
            self._state("finalizing", "Saving selected sections...")
            destination = final_path.with_name(final_path.name.replace("_flv.mp4", ".mp4"))
            if self.outputs.layout == "separate":
                destination = destination.parent / destination.stem / destination.name
                destination.parent.mkdir(exist_ok=True)
            export_recording(converted, timeline, assets, self.outputs,
                             destination,
                             self.ffmpeg_path or "ffmpeg",
                             progress=lambda stage, fraction: self._state("finalizing", stage, progress=fraction))
        except Exception as exc:
            # Keep source and metadata together if export fails (disk full, etc.).
            # Never delete the only captured copy or report a successful export.
            if panel_capture:
                (workspace / "panel.json").write_text(json.dumps(panel_capture.timeline.export(), ensure_ascii=False), encoding="utf-8")
            raise RuntimeError(f"Recording needs recovery in {workspace}: {exc}") from exc
        else:
            # workspace was created directly beneath this recording's output dir.
            if workspace.resolve().parent != final_path.parent or not workspace.name.startswith(".recording-"):
                raise RuntimeError("Unexpected temporary recording path.")
            shutil.rmtree(workspace)

    def _record_live_urls(self, user, room_id, live_urls, output, panel_capture=None):

        min_stream_bytes = 4096
        for index, live_url in enumerate(live_urls, start=1):
            if self._stopped():
                return
            if self.duration:
                logger.info(
                    f"Started recording for {self.duration} seconds "
                    f"(stream {index}/{len(live_urls)})"
                )
            else:
                logger.info(f"Started recording (stream {index}/{len(live_urls)})...")

            buffer_size = 512 * 1024  # 512 KB buffer
            buffer = bytearray()
            bytes_written = 0
            announced_recording = False

            logger.info("[PRESS CTRL + C ONCE TO STOP]")
            if panel_capture:
                panel_capture.reset_start()
            with open(output, "wb") as out_file:
                stop_recording = False
                stream_ended = False
                while not stop_recording and not self._stopped():
                    try:
                        if not self.tiktok.is_room_alive(room_id):
                            logger.info("User is no longer live. Stopping recording.")
                            break

                        start_time = time.time()
                        for chunk in self.tiktok.download_live_stream(live_url):
                            if not announced_recording:
                                self._state("recording", f"Recording @{user}", path=str(output))
                                announced_recording = True
                            if panel_capture:
                                panel_capture.mark_start()
                                panel_capture.feed_stream(chunk)
                            buffer.extend(chunk)
                            bytes_written += len(chunk)
                            if len(buffer) >= buffer_size:
                                out_file.write(buffer)
                                buffer.clear()

                            elapsed_time = time.time() - start_time
                            if self.duration and elapsed_time >= self.duration:
                                stop_recording = True
                                break
                            if self._stopped():
                                stop_recording = True
                                break
                        else:
                            stream_ended = True

                        if stream_ended and bytes_written < min_stream_bytes:
                            break

                    except ConnectionError:
                        if self.mode == Mode.AUTOMATIC:
                            logger.error(Error.CONNECTION_CLOSED_AUTOMATIC)
                            self._wait(TimeOut.CONNECTION_CLOSED * TimeOut.ONE_MINUTE)

                    except (RequestException, HTTPException) as ex:
                        logger.warning(f"Network hiccup, retrying: {ex}")
                        self._wait(2)

                    except KeyboardInterrupt:
                        logger.info("Recording stopped by user.")
                        stop_recording = True

                    except Exception as ex:
                        logger.error(
                            f"Unexpected error during recording: {ex}",
                            exc_info=True,
                        )
                        stop_recording = True

                    finally:
                        if buffer:
                            out_file.write(buffer)
                            buffer.clear()
                        out_file.flush()

            if self._stopped() and bytes_written == 0:
                Path(output).unlink(missing_ok=True)
                return
            if bytes_written >= min_stream_bytes or (self._stopped() and bytes_written):
                break

            logger.warning(
                f"Stream {index}/{len(live_urls)} returned only {bytes_written} bytes. "
                "Trying another CDN/quality..."
            )
        else:
            Path(output).unlink(missing_ok=True)
            raise LiveNotFound(TikTokError.RETRIEVE_LIVE_URL)

        logger.info(f"Recording finished: {Path(output).resolve()}\n")
        self._state("finalizing", "Preparing MP4 (copying original audio and video)...", path=str(output), progress=None)
        VideoManagement.convert_flv_to_mp4(output, self.bitrate, self.ffmpeg_path)

    def check_country_blacklisted(self):
        cookies = self._cookies or {}
        if any(isinstance(cookies.get(name), str) and cookies[name].strip()
               for name in ("sessionid_ss", "sessionid", "sid_tt")):
            # A generic /live redirect does not establish whether this saved
            # session can access the target. Its room API requests still must
            # succeed; a configured cookie is not a verified login.
            return False

        is_blacklisted = self.tiktok.is_country_blacklisted()
        if not is_blacklisted:
            return False

        if self.room_id is None:
            raise TikTokRecorderError(TikTokError.COUNTRY_BLACKLISTED)

        if self.mode == Mode.AUTOMATIC:
            raise TikTokRecorderError(TikTokError.COUNTRY_BLACKLISTED_AUTO_MODE)

        elif self.mode == Mode.FOLLOWERS:
            raise TikTokRecorderError(TikTokError.COUNTRY_BLACKLISTED_FOLLOWERS_MODE)

        return is_blacklisted
