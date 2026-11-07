"""Writes story.json for 003_yuba_county_five: one scene per beat, narration next to the card it shows.

    python build_story.py      (then: python ../../tools/make_cards.py story.json, make_thumbnail.py, BUILD)

Sources for every line: en.wikipedia.org/wiki/Yuba_County_Five (sourced to LA Times, Washington Post, Desert Sun 1978),
mentalfloss.com/article/532063 (undersheriff quote, Melba Madruga quote), dyatlovpass.com/yuba-county-five (photos).
Where sources disagree, Wikipedia's figures are used (19.4 mi, 11.4 mi, three dozen C-rations, butane, ~3 months).
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
WHOOSH = {"path": "../../assets/sfx/whoosh.wav", "at": 0, "volume": 0.05}
POP = {"path": "../../assets/sfx/pop.wav", "at": 0, "volume": 0.06}
BOOM = {"path": "audio/boom.wav", "at": 0, "volume": 0.2}

scenes = []


def scene(name, card, *lines, sfx=None, chapter=False, **extra):
    """lines: plain text, or (text, say) when the voice needs a different spelling."""
    sc = {"image": f"cards/{name}.jpg"}
    if card.get("photo") or card.get("credit"):
        sc["background"] = f"cards/_plate_{name}.jpg"
    sc["card"] = card
    sc["sfx"] = [sfx] if sfx else []
    sc["lines"] = []
    for ln in lines:
        text, say = (ln if isinstance(ln, tuple) else (ln, None))
        sc["lines"].append({"speaker": "narrator", "text": text, **({"say": say} if say else {})})
    if chapter:  # new part of the story: soft whoosh + fade
        sc["sfx"] = sc["sfx"] or [WHOOSH]
        sc["transition"] = "fade"
    sc.update(extra)
    scenes.append(sc)


def framed(photo, credit, **k):
    return {"photo": f"photos/{photo}", "photo_mode": "frame", "credit": credit, **k}


USFS = "Plumas National Forest · USFS photo (public domain)"
DYAT = "via dyatlovpass.com"

# ---------------------------------------------------------------- HOOK
scene("01_hook", {**framed("plumas_snow_tracks.jpg", USFS, photo_w=960),
                  "badge": "HINDI PA NALULUTAS", "headline": "Ang *Yuba County Five*", "headline_size": 100,
                  "sub": "California, 1978"},
      "Nanood lang sila ng basketball.",
      "Pero imbes na umuwi, napadpad ang limang lalaking ito sa nagyeyelong bundok.", sfx=BOOM)
C = "photos/censored/"
scene("01b_lima", {"badge": "HINDI PA NALULUTAS", "big": "5 UMALIS", "big_size": 200,
                   "headline": "*4* patay. *1* nawawala.", "headline_size": 96,
                   "lineup": [{"photo": C + f"{n}.jpg", "label": lab, "dim": lab == "PATAY"} for n, lab in
                              (("jack_madruga", "PATAY"), ("bill_sterling", "PATAY"), ("jackie_huett", "PATAY"),
                               ("ted_weiher", "PATAY"), ("gary_mathias", "NAWAWALA"))],
                   "credit": f"Photos: families / Yuba County Sheriff's Office, {DYAT}"},
      "Apat ang natagpuang patay. Ang isa, hanggang ngayon, nawawala.",
      sfx={**BOOM, "volume": 0.14}, transition="fade")

# ---------------------------------------------------------------- WHO
scene("02_lima", {"badge": "YUBA COUNTY, CALIFORNIA", "big": "5",
                  "headline": "magkakaibigan", "sub": "Kilalanin natin sila"},
      "Kilalanin natin sila.", transition="fade")
for i, (f, name, age, line, credit) in enumerate([
        ("jack_madruga", "Jack Madruga", 30, "Si Jack Madruga, trenta anyos.", f"Photo: Valerie Atiyeh, {DYAT}"),
        ("bill_sterling", "Bill Sterling", 29, "Si Bill Sterling, bente-nuwebe.", f"Photo {DYAT}"),
        ("jackie_huett", "Jackie Huett", 24, "Si Jackie Huett, bente-kuwatro.", f"Photo: courtesy of Tom & Claudia Huett, {DYAT}"),
        ("ted_weiher", "Ted Weiher", 32, "Si Ted Weiher, trenta'y dos.", f"Photo: Yuba County Sheriff's Office, {DYAT}"),
        ("gary_mathias", "Gary Mathias", 25, "At si Gary Mathias, bente-singko.", "Gary Mathias, 1978 · archival photo")], 1):
    scene(f"02{chr(96 + i)}_{f}", {**framed(f"{f}.jpg", credit, photo_w=700), "badge": f"ANG LIMA · {i}/5",
                                   "headline": f"*{name}*", "headline_size": 96, "sub": f"{age} taong gulang"},
          line, sfx=POP)
scene("02f_gators", {"badge": "GATEWAY GATORS", "headline": "Magkakasama sa isang *basketball team*",
                     "sub": "Suportado ng isang programa para sa mga taong may kapansanan"},
      "Lahat sila, may intellectual disability o mental illness.",
      "Magkakasama sila sa isang basketball team, ang Gateway Gators.")
scene("02g_gary", {**framed("gary_mathias.jpg", "Gary Mathias, 1978 · archival photo", photo_w=520),
                   "badge": "SI GARY", "headline": "Army veteran na may *schizophrenia*",
                   "sub": "Nagpapagamot noong 1978", "headline_size": 80},
      "Si Gary, dating sundalo na na-diagnose ng schizophrenia, at nagpapagamot noon.")
scene("02h_sulat", {**framed("ted_weiher_note.jpg", f"Huling sulat sa notebook ni Ted Weiher · LA Times, 1978, {DYAT}", photo_w=560),
                    "badge": "FEBRUARY 25, 1978", "headline": "Kinabukasan, may *laro* sila",
                    "sub": "Special Olympics tournament, Sacramento", "headline_size": 80},
      "At kinabukasan, sila naman ang may laro: ang unang laro nila sa isang Special Olympics tournament. Inihanda na ng ilan sa kanila ang uniporme nila.",
      "Ito ang huling isinulat ni Ted sa notebook niya.")

# ---------------------------------------------------------------- THE NIGHT
scene("03_chico", {**framed("chico_state_kendall_hall.jpg", "Kendall Hall, Chico State · Photo: Frank Schulenburg, CC BY-SA 4.0"),
                   "badge": "FEBRUARY 24, 1978", "headline": "*UC Davis* vs *Chico State*",
                   "sub": "Nanood ang lima sa Chico"},
      ("February 24, 1978. Nagmaneho sila papuntang Chico, para manood ng laro ng UC Davis laban sa Chico State.",
       "February twenty-four, nineteen seventy-eight. Nagmaneho sila papuntang Chico, para manood ng laro ng UC Davis laban sa Chico State."),
      "Panalo ang Davis.", chapter=True)
scene("03b_tindahan", {"badge": "BEHR'S MARKET, CHICO", "headline": "Huling nakita bandang *10 PM*",
                       "sub": "Bumili ng snacks, soda at gatas"},
      "Bandang alas-diyes ng gabi, bumili sila ng snacks, soda, at gatas sa isang tindahan.",
      "Natandaan sila ng tindera, dahil naantala nila ang pagsasara.",
      "Iyon na ang huling kumpirmadong pagkakita sa kanila.")
scene("03c_nawawala", {**framed("missing_poster.jpg", f"Yuba County Sheriff's Department missing persons flyer · {DYAT}", photo_w=900),
                       "badge": "KINAUMAGAHAN", "headline": "*Hindi* sila nakauwi"},
      "Hindi sila nakauwi. Kinaumagahan, nag-report na sa pulis ang mga pamilya.")

# ---------------------------------------------------------------- THE CAR
scene("04_mapa", {**framed("map.png", "Map data © OpenStreetMap contributors", photo_w=960),
                  "badge": "PLUMAS NATIONAL FOREST", "headline": "Natagpuan ang kotse sa *bundok*", "headline_size": 80},
      "Makalipas ang ilang araw, isang forest ranger ang nagsabing nakita niya ang kotse nila noong February 25, sa bundok ng Plumas National Forest.",
      ("Mga 70 milya ito mula sa Chico, at malayo sa daan pauwi.", "Mga pitumpung milya ito mula sa Chico, at malayo sa daan pauwi."), chapter=True)
scene("04b_jack", {**framed("jack_madruga.jpg", f"Jack Madruga · Photo: Valerie Atiyeh, {DYAT}", photo_w=520),
                   "badge": "AYON SA PAMILYA", "headline": "Ayaw ni Jack sa *lamig*",
                   "sub": "Hindi pa siya nakaakyat sa bundok", "headline_size": 80},
      "Ayon sa pamilya ni Jack, ayaw niya sa lamig, at hindi pa siya nakakaakyat sa bundok.")
scene("05_kotse", {**framed("montego_1969.jpg", "Kaparehong modelo (1969 Mercury Montego) · Photo: Sicnag, CC BY 2.0"),
                   "badge": "FEBRUARY 28, 1978", "headline": "Walang sira ang *kotse*",
                   "sub": "Umaandar, may quarter tank pa ng gas", "headline_size": 80},
      "Noong February 28, pinuntahan ito ng mga pulis. Naipit lang ito sa snow. Nang i-hotwire nila, umandar agad, at may quarter tank pa ng gas.",
      "Ayon sa pulis, kaya itong itulak ng limang lalaki palabas ng snow.")
scene("05b_kakaiba", {"badge": "MAS KAKAIBA PA", "headline": "*Walang susi.* Hindi naka-lock.",
                      "sub": "Nakababa ang bintana · walang gasgas ang ilalim"},
      "Wala ang susi. Hindi naka-lock, at nakababa ang isang bintana, bagay na hindi raw gagawin ni Jack.",
      "At kahit lubak-lubak ang daan, walang kahit isang gasgas ang ilalim ng kotse.")
scene("05c_loob", {"badge": "SA LOOB NG KOTSE", "headline": "Mga balot ng snacks, programa ng laro, at *mapa*",
                   "sub": "Nakatupi nang maayos ang mapa ng California"},
      "Sa loob: mga balot ng snacks, programa ng laro, at isang maayos na nakatuping mapa ng California.")
scene("05d_bagyo", {"badge": "SNOWSTORM", "headline": "Itinigil ang *paghahanap*",
                    "sub": "Muntik pang maligaw ang mga naghahanap"},
      "Pero bumagsak ang matinding snowstorm. Muntik pang maligaw ang mga naghahanap, kaya itinigil ito.")

# ---------------------------------------------------------------- WITNESSES
scene("06_saksi", {**framed("plumas_snow_forest.jpg", USFS),
                   "badge": "ISANG SAKSI", "headline": "Si *Joseph Schons*",
                   "sub": "Naipit din sa parehong daan, nang gabi ring iyon"},
      "Balikan natin ang gabi ng February 24. May isang saksi: si Joseph Schons. Naipit din ang kotse niya sa parehong daan, nang gabi ring iyon.",
      "Nakaramdam siya ng sintomas ng atake sa puso, kaya nanatili siya sa loob ng kotse.", chapter=True)
scene("06b_ilaw", {"badge": "MAKALIPAS ANG ANIM NA ORAS", "headline": "Mga *ilaw* at grupo ng tao",
                   "sub": "Isa raw, mukhang babaeng may kargang sanggol"},
      "Makalipas ang mga anim na oras, may nakita siyang mga headlight sa likod niya, at grupo ng tao.",
      "Isa raw, mukhang babaeng may kargang sanggol.",
      "Humingi siya ng tulong. Pero tumahimik sila, at namatay ang mga ilaw.")
scene("06c_umaga", {"badge": "KINAUMAGAHAN", "headline": "Nadaanan niya ang *kotse ng lima*",
                    "sub": "Kinumpirma ng doktor: inatake siya sa puso"},
      "Kinaumagahan, naglakad siya nang walong milya papunta sa isang lodge.",
      "Pauwi, nadaanan nila ang kotse ng lima, sa mismong lugar kung saan niya narinig ang mga boses.")
scene("06d_brownsville", {"badge": "ISA PANG SAKSI", "headline": "Nakita raw sa *Brownsville*",
                          "sub": "Apat na lalaki, sakay ng pulang pickup"},
      "May isa pang saksi. Isang babae sa tindahan sa Brownsville ang nagsabing nakita niya ang apat sa kanila kinabukasan, sakay ng pulang pickup.")

# ---------------------------------------------------------------- THE TRAILER
scene("07_trailer", {"badge": "JUNE 4, 1978", "big": "19.4 MILYA",
                     "headline": "Isang *Forest Service trailer*", "sub": "Mula sa kinaroonan ng kotse"},
      "Lumipas ang mahigit tatlong buwan. Noong June 4, natunaw na ang snow.",
      ("Isang grupo ng motorcyclists ang pumasok sa isang Forest Service trailer, 19.4 milya mula sa kotse.",
       "Isang grupo ng motorcyclists ang pumasok sa isang Forest Service trailer, nineteen point four milya mula sa kotse."), chapter=True)
scene("07b_kama", {**framed("trailer_bunk.jpg", f"Ang kama kung saan natagpuan si Ted Weiher · {DYAT}"),
                   "badge": "SA LOOB NG TRAILER", "headline": "Natagpuan si *Ted Weiher*",
                   "sub": "Nakabalot sa walong kumot, pati ang ulo"},
      "Basag ang bintana. Sa loob, nakita nila si Ted sa kama, nakabalot sa walong kumot, pati ang ulo.")
scene("07c_autopsy", {"badge": "ANG AUTOPSY", "big": "~100 LBS",
                      "headline": "Halos *kalahati* ng timbang niya ang nawala", "sub": "Gutom at hypothermia"},
      "Gutom at hypothermia ang ikinamatay niya. Halos kalahati ng dalawandaang librang timbang niya ang nawala.",
      "Ayon sa haba ng balbas niya, posibleng mga tatlong buwan pa siyang nabuhay.")
scene("07d_paa", {"badge": "ANG KANYANG MGA PAA", "headline": "Frostbite, at *walang sapatos*",
                  "sub": "Halos ma-gangrene na"},
      "Halos ma-gangrene ang mga paa niya dahil sa frostbite. At wala ang sapatos niya.")
scene("07e_mesa", {"badge": "SA MESA", "headline": "Wallet, singsing na *Ted*, at isang *relo*",
                   "sub": "Ayon sa pamilya, hindi kay Ted ang relo"},
      "Sa mesa: ang wallet niya na may pera pa, ang singsing na may nakaukit na Ted, at isang gintong relo, na ayon sa pamilya, hindi kanya.")

# ---------------------------------------------------------------- THE MYSTERY INSIDE
scene("08_pagkain", {"badge": "ANG NAKAKAKILABOT", "big": "36",
                     "headline": "lata ng *C-ration* ang kinain", "sub": "Pero ang pagkaing pang-isang taon, hindi binuksan"},
      "Ang nakakakilabot? Sa labas, may tatlumpu't anim na lata ng C-ration na binuksan at kinain.",
      "Pero ang locker na may pagkaing pang-isang taon, hindi binuksan.")
scene("08b_heater", {"badge": "HINDI GINALAW", "headline": "Heater, fireplace, at makakapal na *damit*",
                     "sub": "May posporo at panggatong pa"},
      "May gas para sa heater, posporo, at mga libro na pwedeng panggatong. May makakapal na damit pa. Pero wala ni isang ginamit.")
scene("08c_p38", {"badge": "ISANG CLUE", "headline": "Binuksan gamit ang *P-38*",
                  "sub": "Army can opener: sina Gary at Jack lang ang marunong"},
      "At ang mga lata? Binuksan gamit ang P-38, isang can opener ng Army. Sa lima, sina Gary at Jack lang ang marunong gumamit nito.")

# ---------------------------------------------------------------- THE OTHERS
scene("09_labi", {"badge": "KINABUKASAN", "headline": "Natagpuan sina *Madruga* at *Sterling*",
                  "sub": "Magkabilang gilid ng daan, 11.4 milya mula sa kotse"},
      ("Kinabukasan, natagpuan ang mga labi nina Jack Madruga at Bill Sterling, sa magkabilang gilid ng daan, 11.4 milya mula sa kotse.",
       "Kinabukasan, natagpuan ang mga labi nina Jack Madruga at Bill Sterling, sa magkabilang gilid ng daan, eleven point four milya mula sa kotse."), chapter=True)
scene("09b_huett", {**framed("jackie_huett.jpg", f"Jackie Huett · Photo: courtesy of Tom & Claudia Huett, {DYAT}", photo_w=480),
                    "badge": "MAKALIPAS ANG DALAWANG ARAW", "headline": "Ang ama ni *Jackie* ang nakakita",
                    "sub": "2 milya mula sa trailer", "headline_size": 80},
      "Makalipas ang dalawang araw, ang ama mismo ni Jackie Huett ang nakakita sa mga labi ng anak niya, dalawang milya mula sa trailer.")

# ---------------------------------------------------------------- GARY
scene("10_gary", {**framed("gary_mathias.jpg", "Gary Mathias, 1978 · archival photo", photo_w=560),
                  "badge": "HANGGANG NGAYON", "headline": "Nawawala pa rin si *Gary Mathias*",
                  "sub": "Nasa trailer ang tennis shoes niya", "headline_size": 80},
      "Pero si Gary Mathias? Nasa trailer ang tennis shoes niya. Posibleng isinuot niya ang sapatos ni Ted, at umalis.",
      "Ipinakalat ang litrato niya sa mga mental institution sa buong California. Pero hanggang ngayon, hindi pa siya natatagpuan.", chapter=True)

# ---------------------------------------------------------------- THEORY & ENDING
scene("11_teorya", {"badge": "ANG TEORYA NG PULIS", "headline": "*Naligaw* sila",
                    "sub": "Baka papunta sa mga kaibigan ni Gary sa Forbestown"},
      "Ang teorya ng pulis: baka papunta sila sa mga kaibigan ni Gary sa Forbestown, at naligaw.",
      "Nang maipit ang kotse, sinundan daw nila ang bakas ng isang snowcat, umaasang may masisilungan.", chapter=True)
scene("11b_takot", {"badge": "AT SA TRAILER?", "headline": "Baka *natakot* silang gumamit",
                    "sub": "Naka-lock ang trailer: baka mapagbintangan silang magnanakaw"},
      "Pagdating sa trailer, baka natakot silang gamitin ang mga gamit doon, dahil baka mapagbintangan silang magnanakaw.")
scene("11c_bizarre", {**framed("la_times_1978.jpg", f"Los Angeles Times, 1978 · {DYAT}", photo_w=960),
                      "badge": "PERO...", "headline": "This case is *bizarre as hell.*",
                      "sub": "Ayon sa undersheriff ng Yuba County", "headline_size": 80},
      "Pero hindi nito masagot ang lahat. Sino ang mga taong nakita ni Schons? Bakit nila iniwan ang kotseng umaandar pa?",
      "Sabi nga ng undersheriff ng Yuba County: this case is bizarre as hell.")
scene("11d_ina", {"badge": "ANG INA NI JACK", "headline": "\"Alam naming may *nagpilit* sa kanila\"",
                  "sub": "Melba Madruga"},
      "At ayon sa ina ni Jack: alam naming may nagpilit sa kanila.")
scene("12_tanong", {"big": "?", "headline": "Ano sa tingin *mo* ang nangyari?",
                    "sub": "I-comment mo sa baba!"},
      "Ikaw, ano sa tingin mo ang nangyari? I-comment mo sa baba.",
      sfx={"path": "../../assets/sfx/ding.wav", "at": 0, "volume": 0.07})
scene("13_follow", {"badge": "SALAMAT SA PANONOOD!", "headline": "Gusto mo pa ng mga ganitong *kwento?*",
                    "sub": "I-follow ang Juan Fact a Day!", "mascot": True},
      ("Kung gusto mo pa ng mga ganitong kwento, i-follow mo ako, dito sa Juan Fact a Day!",
       "Kung gusto mo pa ng mga ganitong kwento, i-follow mo ako, dito sa Hu-wan Fact a Day!"),
      sfx=POP, hold_after=0.8)

# the note is about the NEXT day, so it comes right after the Feb 24 game
_i = next(i for i, x in enumerate(scenes) if "02h_sulat" in x["image"])
_note = scenes.pop(_i)
scenes.insert(next(i for i, x in enumerate(scenes) if "03_chico" in x["image"]) + 1, _note)

story = {
    "extends": "../../channel.json",
    "title": "Ang Yuba County Five",
    "output": {"path": "003_yuba_county_five.mp4", "aspect": "9:16"},
    "voices": {"narrator": {"engine": "edge", "voice": "fil-PH-AngeloNeural", "rate": "+5%", "pitch": "-2Hz"}},
    "defaults": {"max_pause": 0.35},
    "music": {"path": "audio/drone.wav", "volume": 0.11},
    "scenes": scenes,
    "thumbnail": {
        "top": 420, "badge": "HINDI PA NALULUTAS", "big": "5 UMALIS", "big_size": 230,
        "headline": "4 patay. *1 nawawala.*", "headline_size": 96,
        "lineup": [{"photo": f"photos/censored/{f}.jpg", "label": "PATAY", "dim": True}
                   for f in ("jack_madruga", "bill_sterling", "jackie_huett", "ted_weiher")]
                  + [{"photo": "photos/censored/gary_mathias.jpg", "label": "NAWAWALA"}],
        "sticker": "TOTOONG KASO, 1978", "mascot": False},
}
(HERE / "story.json").write_text(json.dumps(story, ensure_ascii=False, indent=1), encoding="utf-8")
print(len(scenes), "scenes")
