@echo off
rem Start the episode studio at http://127.0.0.1:8765 (pass --port N to change it).
setlocal
pushd "%~dp0" || exit /b 1
uv run python -m video_pipeline.studio %*
set "studio_exit_code=%errorlevel%"
popd
exit /b %studio_exit_code%
