@echo off
REM ---------------------------------------------------------------
REM  Genera dist\consolidador_dosis.exe  (ejecutable unico, sin
REM  necesidad de tener Python instalado en la maquina destino)
REM ---------------------------------------------------------------
cd /d "%~dp0"

python -m pip install --upgrade -r requirements.txt || goto :error

python -m PyInstaller --noconfirm --clean --onefile --console ^
    --name consolidador_dosis ^
    --collect-all pdfplumber ^
    --collect-all pdfminer ^
    --collect-all pypdfium2 ^
    --collect-all pypdfium2_raw ^
    --hidden-import laboratorios ^
    --hidden-import laboratorios.base ^
    --hidden-import laboratorios.dosicontrol ^
    consolidador_dosis.py || goto :error

echo.
echo ================================================================
echo  Listo:  %~dp0dist\consolidador_dosis.exe
echo ================================================================
pause
exit /b 0

:error
echo.
echo  ERROR al construir el ejecutable.
pause
exit /b 1
