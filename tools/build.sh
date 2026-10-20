#!/bin/bash
# Сборка одного файла PyInstaller. Те же флаги, что в .github/workflows/build.yml для Windows.
#   tools/build.sh         — обычная сборка (как для друга)
#   tools/build.sh --dev   — с маркером DEV_BUILD: в упакованной версии работают служебные OKF_* (только для прогона на маке)
set -e
cd "$(dirname "$0")/.."
PY=.venv/bin/python
EXTRA=""
if [ "$1" = "--dev" ]; then
  mkdir -p build/devmark
  touch build/devmark/DEV_BUILD
  EXTRA="--add-data build/devmark/DEV_BUILD:."
fi
$PY -m PyInstaller --noconfirm --clean --onefile --windowed \
  --name OK-Friends --icon assets/icon.ico \
  --collect-all playwright \
  $EXTRA ok_friends.py
ls -la dist/
