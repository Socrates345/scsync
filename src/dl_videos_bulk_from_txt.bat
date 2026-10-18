@echo off
setlocal enabledelayedexpansion

set URLFILE=src\videos_bulk.txt

if not exist "%URLFILE%" (
    echo Could not find %URLFILE% in this folder.
    echo Create a file called %URLFILE% with one YouTube link per line.
    pause
    exit /b 1
)

set /a COUNT=0
for /f "usebackq delims=" %%A in ("%URLFILE%") do set /a COUNT+=1

set /a i=0
for /f "usebackq delims=" %%A in ("%URLFILE%") do (
    set /a i+=1
    echo Run !i! of %COUNT%: %%A
    python src\ytdl.py -l "%%A" --video
    if errorlevel 1 (
        echo Run !i! failed with error level !errorlevel!
    )
)

echo Done. Processed %COUNT% links.
pause