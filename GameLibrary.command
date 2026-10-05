#!/usr/bin/env bash
# Game Library: instala o que falta (so na primeira vez) e mostra um menu.
# No macOS, da para abrir com dois cliques. No Linux: bash GameLibrary.command
cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1
VPY=".venv/bin/python"

achar_python() {
    for candidato in python3.13 python3.12 python3.11 python3 python; do
        if command -v "$candidato" >/dev/null 2>&1 &&
            "$candidato" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            echo "$candidato"
            return 0
        fi
    done
    return 1
}

preparar() {
    if [ ! -x "$VPY" ]; then
        local py
        py=$(achar_python) || {
            echo "Nao encontrei o Python 3.11 ou mais novo."
            echo "Instale em https://www.python.org/downloads/ e abra este arquivo de novo."
            return 1
        }
        echo "Criando o ambiente (so na primeira vez)..."
        "$py" -m venv .venv || { echo "Nao consegui criar o ambiente."; return 1; }
    fi
    if [ ! -f .venv/.instalado ]; then
        echo "Instalando as dependencias (pode levar alguns minutos)..."
        "$VPY" -m pip install -e . || {
            echo
            echo "Falha ao instalar. Confira a conexao com a internet e tente de novo."
            return 1
        }
        touch .venv/.instalado
    fi
}

comando() {
    clear 2>/dev/null
    "$VPY" -m gamelibrary.cli "$@"
    echo
    read -r -p "Pressione Enter para voltar ao menu... " _
}

abrir() {
    clear 2>/dev/null
    echo "O app vai abrir no navegador, em http://127.0.0.1:8765"
    echo "Deixe esta janela aberta enquanto usa o app. Ctrl+C encerra e volta ao menu."
    echo
    trap 'echo' INT
    "$VPY" -m gamelibrary.cli serve
    trap - INT
}

loja() {
    while true; do
        clear 2>/dev/null
        echo "Sincronizar qual loja?"
        echo
        echo "  1  Steam"
        echo "  2  GOG"
        echo "  3  Epic"
        echo "  4  Lista manual"
        echo "  0  Voltar"
        echo
        read -r -p "Escolha uma opcao: " op || return
        case "$op" in
            1) comando sync steam; return ;;
            2) comando sync gog; return ;;
            3) comando sync epic; return ;;
            4) comando sync manual; return ;;
            0) return ;;
        esac
    done
}

if ! preparar; then
    echo
    read -r -p "Pressione Enter para sair... " _
    exit 1
fi

while true; do
    clear 2>/dev/null
    echo "=============================================="
    echo "  Game Library"
    echo "=============================================="
    echo
    echo "  1  Abrir o app"
    echo "  2  Sincronizar tudo (Steam, GOG, Epic e lista manual)"
    echo "  3  Sincronizar uma loja so"
    echo "  4  Configurar a Steam (chave e ID)"
    echo "  5  Entrar na GOG"
    echo "  6  Entrar na Epic"
    echo "  7  Atualizar o programa (reinstalar as dependencias)"
    echo "  0  Sair"
    echo
    read -r -p "Escolha uma opcao: " op || exit 0
    case "$op" in
        1) abrir ;;
        2) comando sync ;;
        3) loja ;;
        4) comando steam-setup ;;
        5) comando gog-login ;;
        6) comando epic-login ;;
        7) rm -f .venv/.instalado; preparar; echo; read -r -p "Pressione Enter para voltar ao menu... " _ ;;
        0) exit 0 ;;
    esac
done
