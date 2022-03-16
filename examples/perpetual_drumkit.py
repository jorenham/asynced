import asyncio
from asynced import ensure_perpetual, Perpetual


async def start_metronome(bpm: float, beats: int):
    i = 1
    while True:
        yield i

        i = (i % beats) + 1

        await asyncio.sleep(60 / bpm)


def base_drum(beat: int):
    if beat == 1:
        print('DUM')


def snare_drum(beat: int):
    if beat == 3:
        print('DA')


def hihat(beat: int):
    print('ts')


async def amain():
    metronome = ensure_perpetual(start_metronome(100, 4))

    for item in [base_drum, snare_drum, hihat]:
        metronome.add_result_callback(item)

    bar = 0
    async for beat in metronome:
        print()

        if beat == 4:
            bar += 1

            if bar < 4:
                print('--------')
            else:
                print('========')
                metronome.stop()


asyncio.run(amain())
