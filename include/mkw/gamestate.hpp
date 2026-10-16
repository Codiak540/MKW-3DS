//
// Created by george on 10/15/26.
//

#ifndef MKW_3DS_GAMESTATE_H
#define MKW_3DS_GAMESTATE_H

struct GameState {
    u64 lastFrameTime = 0; // Get initial tick timestamp in ms
    u32 frameCount = 0;
    float fps = 0.0f;
};

inline GameState gameState;

#endif //MKW_3DS_GAMESTATE_H
