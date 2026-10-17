#pragma once
namespace mkw::display {
// Native top LCD. Menus previously mapped to a letterboxed 400x225 canvas;
// all presentation now uses the full 5:3 frame requested for this build.
inline constexpr float Width=400.f,Height=240.f,Aspect=Width/Height;
inline constexpr float MenuScaleX=Width/608.f,MenuScaleY=Height/456.f;
static_assert(Width*3==Height*5);
// Races fill the whole 5:3 screen on every console. Set true to render the
// retail 4:3 (the race canvas is 608x456) in a centred 320x240 window with
// black bars either side instead, which is also 20% fewer pixels to fill.
inline constexpr bool RaceLetterbox=false;
inline constexpr float RaceAspect=4.f/3.f,RaceWidth=Height*RaceAspect,RaceBar=(Width-RaceWidth)*.5f;
static_assert(RaceWidth==320.f&&RaceBar==40.f);
}
