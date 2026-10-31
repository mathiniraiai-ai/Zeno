# Reference voices (optional)

Drop a short clean sample (10 to 30 seconds, mono or stereo `.wav`) of each character here, named exactly like the
speaker in the script:

    input/voices/Man.wav
    input/voices/Robot.wav

For a script line like `L2 Robot: ...`, Zeno then knows what the Robot sounds like even when there are only a few clips,
and cuts any line the wrong character speaks. Without samples Zeno learns each voice from the script itself, which
works when there are several clips of each character.
