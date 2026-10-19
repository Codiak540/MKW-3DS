#pragma once
#include <cstddef>
#include <cstdint>
#include <vector>

namespace mkw {
// Indexed archive of menu runtime assets (tools/pack_menu_assets.py). One
// handle stays open, so each asset costs a seek and a read rather than a file
// open and close. Paths not in the archive report false; callers then use the
// loose file. Thread-safe: the asset loader and main thread may both read.
namespace pack {
// Opens (once) "menu.pak", "race.pak" and "o3ds.pak" relative to the current asset directory.
void open();
void close();
bool contains(const char* path);
// Reads the whole asset into out (resized). False if absent or unreadable.
bool read(const char* path,std::vector<uint8_t>& out);
// Size of an asset, or -1 if absent.
long size(const char* path);
// Reads exactly `bytes` (must equal size(path)) into destination.
bool readInto(const char* path,void* destination,size_t bytes);
// Reads `bytes` starting `offset` bytes into the asset; false past its end.
bool readRange(const char* path,size_t offset,void* destination,size_t bytes);
// True when o3ds.pak is open: it then holds the whole Old 3DS overlay, so a
// models/o3ds path it lacks has no loose file either (the builder ships one
// form or the other) and the caller skips the SD open of an absent file.
bool overlayPacked();
}
}
