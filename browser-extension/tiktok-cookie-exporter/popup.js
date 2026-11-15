import { buildExport, FILE_NAME, SITE } from "./cookies.mjs";

const button = document.getElementById("export");
const status = document.getElementById("status");

function show(message, kind) {
  status.textContent = message;
  status.dataset.kind = kind;
}

button.addEventListener("click", async () => {
  button.disabled = true;
  show("Preparing your local login file…", "working");
  let downloadUrl;
  try {
    const cookies = await chrome.cookies.getAll({ url: SITE + "/" });
    const payload = buildExport(cookies);
    const blob = new Blob([JSON.stringify(payload, null, 2) + "\n"], { type: "application/json" });
    downloadUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = FILE_NAME;
    document.body.appendChild(link);
    link.click();
    link.remove();
    show("Login file downloaded. In the recorder, choose TikTok login → Import cookies and select it.", "success");
  } catch (error) {
    // Never expose browser error details or cookie values in the popup.
    const noLogin = error instanceof Error && error.message.startsWith("Sign into tiktok.com");
    show(noLogin ? error.message : "Could not export TikTok login. Reload this extension and try again.", "error");
  } finally {
    button.disabled = false;
    // Let Chrome start the download before releasing the local object URL.
    if (downloadUrl) setTimeout(() => URL.revokeObjectURL(downloadUrl), 10000);
  }
});
