#!/bin/zsh
set -eu
cd "${0:A:h}/.."
mkdir -p build
xcrun clang++ -O2 -std=c++17 -fobjc-arc -framework Cocoa -framework Metal -framework QuartzCore app/main.mm -o build/metal-doom.tmp
mv -f build/metal-doom.tmp build/metal-doom
mkdir -p "build/Metal Doom.app/Contents/MacOS"
cp build/metal-doom "build/Metal Doom.app/Contents/MacOS/metal-doom.tmp"
mv -f "build/Metal Doom.app/Contents/MacOS/metal-doom.tmp" "build/Metal Doom.app/Contents/MacOS/metal-doom"
cat > "build/Metal Doom.app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd"><plist version="1.0"><dict><key>CFBundleExecutable</key><string>metal-doom</string><key>CFBundleIdentifier</key><string>org.metaldoom.standalone</string><key>CFBundleName</key><string>Metal Doom</string><key>CFBundlePackageType</key><string>APPL</string><key>NSHighResolutionCapable</key><true/></dict></plist>
PLIST

mkdir -p "build/Metal Doom.app/Contents/Resources"
for asset in doom.metal initial-memory.bin; do
  if [[ -f "build/$asset" ]]; then cp "build/$asset" "build/Metal Doom.app/Contents/Resources/$asset"; fi
done
cp LICENSE CREDITS.md "build/Metal Doom.app/Contents/Resources/"
cp vendor/PROVENANCE.md "build/Metal Doom.app/Contents/Resources/PROVENANCE.md"
cp vendor/tinylibc/LICENSE "build/Metal Doom.app/Contents/Resources/tinylibc-LICENSE"
