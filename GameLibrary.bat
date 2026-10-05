@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Game Library
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "VPY=.venv\Scripts\python.exe"

call :preparar
if errorlevel 1 (
    echo.
    pause
    exit /b 1
)

:menu
cls
echo ==============================================
echo   Game Library
echo ==============================================
echo.
echo   1  Abrir o app
echo   2  Sincronizar tudo ^(Steam, GOG, Epic e lista manual^)
echo   3  Sincronizar uma loja so
echo   4  Configurar a Steam ^(chave e ID^)
echo   5  Entrar na GOG
echo   6  Entrar na Epic
echo   7  Atualizar o programa ^(reinstalar as dependencias^)
echo   0  Sair
echo.
set "OP="
set /p "OP=Escolha uma opcao: "
if "%OP%"=="1" goto :abrir
if "%OP%"=="2" (
    call :comando "sync"
    goto :menu
)
if "%OP%"=="3" goto :loja
if "%OP%"=="4" (
    call :comando "steam-setup"
    goto :menu
)
if "%OP%"=="5" (
    call :comando "gog-login"
    goto :menu
)
if "%OP%"=="6" (
    call :comando "epic-login"
    goto :menu
)
if "%OP%"=="7" goto :atualizar
if "%OP%"=="0" exit /b 0
goto :menu

:abrir
cls
echo O app vai abrir no navegador, em http://127.0.0.1:8765
echo Deixe esta janela aberta enquanto usa o app. Para encerrar, feche esta janela.
echo.
"%VPY%" -m gamelibrary.cli serve
goto :menu

:loja
cls
echo Sincronizar qual loja?
echo.
echo   1  Steam
echo   2  GOG
echo   3  Epic
echo   4  Lista manual
echo   0  Voltar
echo.
set "OP2="
set /p "OP2=Escolha uma opcao: "
if "%OP2%"=="1" (
    call :comando "sync steam"
    goto :menu
)
if "%OP2%"=="2" (
    call :comando "sync gog"
    goto :menu
)
if "%OP2%"=="3" (
    call :comando "sync epic"
    goto :menu
)
if "%OP2%"=="4" (
    call :comando "sync manual"
    goto :menu
)
if "%OP2%"=="0" goto :menu
goto :loja

:atualizar
if exist ".venv\.instalado" del ".venv\.instalado"
call :preparar
echo.
pause
goto :menu

:comando
cls
"%VPY%" -m gamelibrary.cli %~1
echo.
pause
exit /b 0

:preparar
if exist "%VPY%" goto :dependencias
call :achar_python
if not defined PY (
    echo Nao encontrei o Python 3.11 ou mais novo neste computador.
    echo.
    echo Instale pelo site oficial e MARQUE a opcao "Add python.exe to PATH":
    echo   https://www.python.org/downloads/windows/
    echo Depois abra este arquivo de novo.
    start "" "https://www.python.org/downloads/windows/"
    exit /b 1
)
echo Criando o ambiente ^(so na primeira vez^)...
%PY% -m venv .venv
if errorlevel 1 (
    echo Nao consegui criar o ambiente.
    exit /b 1
)
:dependencias
if exist ".venv\.instalado" exit /b 0
echo Instalando as dependencias ^(pode levar alguns minutos^)...
"%VPY%" -m pip install -e .
if errorlevel 1 (
    echo.
    echo Falha ao instalar. Confira a conexao com a internet e tente de novo.
    exit /b 1
)
echo ok> ".venv\.instalado"
exit /b 0

:achar_python
set "PY="
for %%C in ("py -3" "python" "python3") do (
    if not defined PY (
        %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
        if not errorlevel 1 set "PY=%%~C"
    )
)
exit /b 0
