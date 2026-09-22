# SPDX-FileCopyrightText: 2026 SpanSynth contributors
# SPDX-License-Identifier: Apache-2.0

"""Fine40 instrument groups, adapted from YourMT3 (Apache-2.0)."""

_FINE_PROGRAM_GROUPS = {
    "Acoustic Piano": (0, 1, 3, 6, 7),
    "Electric Piano": (2, 4, 5),
    "Chromatic Percussion": tuple(range(8, 16)),
    "Organ": tuple(range(16, 24)),
    "Acoustic Guitar": tuple(range(24, 26)),
    "Clean Electric Guitar": tuple(range(26, 29)),
    "Distorted Electric Guitar": tuple(range(29, 32)),
    "Acoustic Bass": (32, 35),
    "Electric Bass": (33, 34, 36, 37, 38, 39),
    "Violin": (40,),
    "Viola": (41,),
    "Cello": (42,),
    "Contrabass": (43,),
    "Orchestral Harp": (46,),
    "Timpani": (47,),
    "String Ensemble": (48, 49, 44, 45),
    "Synth Strings": (50, 51),
    "Choir and Voice": (52, 53, 54),
    "Orchestra Hit": (55,),
    "Trumpet": (56, 59),
    "Trombone": (57,),
    "Tuba": (58,),
    "French Horn": (60,),
    "Brass Section": (61, 62, 63),
    "Soprano/Alto Sax": (64, 65),
    "Tenor Sax": (66,),
    "Baritone Sax": (67,),
    "Oboe": (68,),
    "English Horn": (69,),
    "Bassoon": (70,),
    "Clarinet": (71,),
    "Pipe": (73, 72, 74, 75, 76, 77, 78, 79),
    "Synth Lead": tuple(range(80, 88)),
    "Synth Pad": tuple(range(88, 96)),
    "Singing Voice": (100,),
    "Singing Voice (chorus)": (101,),
    "Drums": (128,),
}

PROGRAM_GROUPS = {**_FINE_PROGRAM_GROUPS, "Sitar": (104,), "Banjo": (105,), "Fiddle": (110,)}
PROGRAM_TO_CATEGORY = {program: index for index, programs in enumerate(PROGRAM_GROUPS.values()) for program in programs}

def program_category_class_index(program, mode="fine40"):
    if mode != "fine40":
        raise ValueError("The released checkpoint uses Fine40 instrument categories")
    if program not in PROGRAM_TO_CATEGORY:
        raise ValueError(f"MIDI program {program} is outside the checkpoint's Fine40 vocabulary; choose a supported program in your MIDI editor")
    return PROGRAM_TO_CATEGORY[program]

def program_category_count(mode="fine40"):
    if mode != "fine40":
        raise ValueError("The released checkpoint uses Fine40 instrument categories")
    return len(PROGRAM_GROUPS)

