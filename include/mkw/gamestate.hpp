//
// Created by george on 10/15/26.
//

#ifndef MKW_3DS_GAMESTATE_H
#define MKW_3DS_GAMESTATE_H

struct GameState {
    static inline u64 lastFrameTime = 0; // Get initial tick timestamp in ms
    static inline u32 frameCount = 0;
    static inline float fps = 0.0f;
    static inline float frameMs = 0.0f;
};

#endif //MKW_3DS_GAMESTATE_H
