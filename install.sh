#!/bin/sh
# Installing igarchive on macOS and Linux.
#
# Creates an isolated Python environment in .venv and installs the program into
# it. Changes nothing else on the system without asking.

set -eu

ROOT=$(cd "$(dirname "$0")" && pwd)
VENV="$ROOT/.venv"
MIN_MAJOR=3
MIN_MINOR=9

say()  { printf '%s\n' "$*"; }
fail() { printf '\nErreur : %s\n' "$*" >&2; exit 1; }

# -- 1. find a recent enough Python -----------------------------------------

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c "import sys; sys.exit(0 if sys.version_info >= ($MIN_MAJOR,$MIN_MINOR) else 1)" 2>/dev/null; then
        PYTHON=$(command -v "$candidate")
        break
    fi
done

if [ -z "$PYTHON" ]; then
    say "Aucun Python $MIN_MAJOR.$MIN_MINOR ou plus recent n'a ete trouve."
    case "$(uname -s)" in
        Darwin) say "  Installe-le avec :  brew install python3" ;;
        Linux)  say "  Debian/Ubuntu :  sudo apt install python3 python3-venv"
                say "  Fedora        :  sudo dnf install python3" ;;
    esac
    exit 1
fi
say "Python          : $PYTHON ($("$PYTHON" -c 'import platform;print(platform.python_version())'))"

# -- 2. isolated environment ------------------------------------------------

if [ ! -d "$VENV" ]; then
    "$PYTHON" -m venv "$VENV" 2>/dev/null || fail \
"creation de l'environnement impossible.
Sous Debian et Ubuntu, le module venv est dans un paquet separe :
    sudo apt install python3-venv"
    say "Environnement   : cree dans .venv"
else
    say "Environnement   : .venv existe deja, reutilise"
fi

"$VENV/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true

# -- 3. install -------------------------------------------------------------

say "Installation    : en cours..."
EXTRAS=""
if [ "${1:-}" = "--browsers" ]; then
    EXTRAS="[browsers]"
    say "                  avec la lecture des cookies de Chrome, Safari, Edge et Brave"
fi
"$VENV/bin/python" -m pip install --quiet -e "$ROOT$EXTRAS" || fail "l'installation a echoue."

VERSION=$("$VENV/bin/igarchive" --version 2>/dev/null || echo "?")
say "Installe        : $VERSION"

# -- 4. make the command reachable ------------------------------------------

BIN="$VENV/bin/igarchive"
say ""
if command -v igarchive >/dev/null 2>&1 && [ "$(command -v igarchive)" = "$BIN" ]; then
    say "La commande « igarchive » est deja accessible."
else
    case "${SHELL##*/}" in
        zsh)  PROFILE="$HOME/.zshrc"  ;;
        bash) PROFILE="$HOME/.bashrc" ;;
        *)    PROFILE="" ;;
    esac

    LINE="alias igarchive=\"$BIN\""
    if [ -n "$PROFILE" ] && [ -t 0 ]; then
        printf 'Ajouter la commande « igarchive » a %s ? [o/N] ' "$PROFILE"
        read -r answer
        case "$answer" in
            [oOyY]*)
                if grep -qF "$BIN" "$PROFILE" 2>/dev/null; then
                    say "Deja present dans $PROFILE."
                else
                    printf '\n# igarchive\n%s\n' "$LINE" >> "$PROFILE"
                    say "Ajoute a $PROFILE — ouvre un nouveau terminal pour en profiter."
                fi
                ;;
            *) say "Rien ajoute." ;;
        esac
    fi
    say ""
    say "Sans alias, lance le programme par son chemin complet :"
    say "    $BIN"
fi

say ""
say "Pour demarrer, ouvre l'interface :"
say "    $BIN"
say ""
say "Pense a demander des maintenant ton export Instagram : il met plusieurs"
say "heures a 48 h a arriver, et lui seul contient les dates d'enregistrement."
say "    https://accountscenter.instagram.com/info_and_permissions/dyi/"
