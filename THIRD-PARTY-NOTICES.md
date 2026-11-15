# Third-party notices

The original recorder is Copyright (c) 2023 Michele, MIT licensed; the full
notice is preserved in `LICENSE`.

`DEPENDENCIES.json` lists the exact runtime dependency versions. License texts
from those installed distributions are included in `THIRD-PARTY-LICENSES` and
the portable ZIP. Python and Tcl/Tk notices are included there too. Consult each
notice for its terms; the application's MIT license does not replace them.

The Windows launcher is built with PyInstaller under its GPL exception allowing
redistribution of generated applications. The build specification disables UPX.
See https://pyinstaller.org/en/stable/license.html .

FFmpeg binaries are **not included in this project's download**. On first use,
the application downloads the user's own copy of the version-pinned Gyan.dev
essentials build. That separately downloaded build is GPLv3. Its archive license
is retained in the engine's cache folder. Build information, source references,
included libraries and license details are available from:

- https://www.gyan.dev/ffmpeg/builds/
- https://ffmpeg.org/legal.html
- https://github.com/FFmpeg/FFmpeg

The application's code interacts with FFmpeg through a subprocess. Advanced
users can supply their own compatible engine. No FFmpeg libraries are linked
into this application's executable.
