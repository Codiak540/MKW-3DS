//
// Created by george on 10/15/26.
//

#ifndef MKW_3DS_GAMESTATE_H
#define MKW_3DS_GAMESTATE_H

#include <3ds.h>

// Race frame rate for the bottom screen's FPS readout: the race loop calls
// frameDone() once per submitted frame (src/main.cpp).
struct GameState {
    static inline u64 lastFrameTime = 0; // osGetTime() (ms) when the current one-second window began; 0 = not started
    static inline u32 frameCount = 0;
    static inline float fps = 0.0f;
    static inline float frameMs = 0.0f;  // mean ms per drawn frame over that second

    // Once per drawn race frame, after renderer.end() (src/main.cpp). Loop
    // passes that only step the simulation (Old 3DS at 30 FPS) do not count.
    static void frameDone() {
        const u64 now = osGetTime();
        if (!lastFrameTime) { lastFrameTime = now; frameCount = 0; return; }
        ++frameCount;
        const u64 elapsed = now - lastFrameTime;
        if (elapsed >= 1000) {
            fps = float(frameCount) * 1000.0f / float(elapsed);
            frameMs = float(elapsed) / float(frameCount);
            frameCount = 0;
            lastFrameTime = now;
        }
    }
    // A race load stalls the loop: start a fresh window afterwards.
    static void reset() { lastFrameTime = 0; frameCount = 0; fps = 0.0f; frameMs = 0.0f; }
};

#endif //MKW_3DS_GAMESTATE_H
