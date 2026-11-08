"""Writes story.json for 004_mary_grace_baloyo: one scene per beat, narration next to the card it shows.

    python build_story.py      (then: python ../../tools/make_cards.py story.json, make_thumbnail.py, BUILD)

Sources for every line:
  en.wikipedia.org/wiki/Mary_Grace_Baloyo (birth Oct 30 1973, PAFFS 1997, 15th Strike Wing, Sangley -> Clark flight,
      Fil-Homes / Brgy. Mabiga, "first female member of the AFP to receive the Medal of Valor", only female PAF recipient)
  philstar.com/headlines/2001/03/30/90237/heroine-comes-home (eldest of four, mother reluctant, Camp Abubakar medals,
      co-pilot Capt. Ben Nasayao bailed out seconds before, empty lot, father: "more than 200 families", fiance a pilot)
  filipiknow.net/mary-grace-baloyo ("Amazing Grace", one of six women combat pilots, Arroyo conferred MoV + Captain,
      resident: "She died so we might live", Bacolod memorial statue)
  Inquirer, 4 Apr 2001: "Gloria Confers Medal of Valor on Heroine Pilot"; 9 Apr 2001: "She Showed All of Us How a Soldier Should Die"
Age: born 30 Oct 1973, died 26 Mar 2001 -> 27 (Philstar also says 27; FilipiKnow's "26" is wrong).
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
WHOOSH = {"path": "../../assets/sfx/whoosh.wav", "at": 0, "volume": 0.05}
POP = {"path": "audio/transition.wav", "at": 0, "volume": 0.06}  # soft swish + warm tone (audio/make_transition.py), not the cartoony pop
DING = {"path": "../../assets/sfx/ding.wav", "at": 0, "volume": 0.07}
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
    return {"photo": f"photos/{photo}", "photo_mode": "frame", **k}  # credit lives in caption.txt, not on screen


CADET = "Mary Grace Baloyo bilang aviation cadet · Wikimedia Commons (public domain)"
BRONCO = "PAF OV-10 Bronco (hindi ang mismong eroplano) · 15th Strike Wing, PAF (public domain)"


def full(photo, credit, focus=(0.5, 0.5), **k):
    """Real photo, full screen (the story's actual picture matters more than the card template)."""
    return {"photo": f"photos/{photo}", "photo_mode": "full", "photo_focus": list(focus), **k}  # credit in caption.txt
BALIK = "PAF OV-10 Bronco, Balikatan 2019 · Sgt. Ariel J. Solomon, U.S. DoD (public domain)"
CRASH = "Crash site, Mabiga, 2001 · Pinoy Aviators FB page, via FilipiKnow (not freely licensed)"
STATUE = "Memorial statue, Bacolod City · Pinoy Aviators FB page, via FilipiKnow (not freely licensed)"

# ---------------------------------------------------------------- HOOK
# Few scenes, each held for several sentences, so the picture doesn't flip every 2 seconds.
scene("01_hook", {**framed("baloyo_cadet.jpg", CADET, photo_w=700),
                  "badge": "ALAM MO BA?", "headline": "Ang *kauna-unahang babae*", "headline_size": 76,
                  "sub": "na ginawaran ng Medal of Valor"},
      "Alam mo ba kung sino ang kauna-unahang babaeng sundalo sa Pilipinas na ginawaran ng Medal of Valor?",
      "Ang pinakamataas na parangal ng militar, para sa katapangan.",
      "Nang bumabagsak ang eroplano niya, may pagkakataon siyang mag-eject at mabuhay. Pero hindi niya ito ginawa.", sfx=BOOM)

# ---------------------------------------------------------------- WHO
scene("02_pangalan", {**full("baloyo_cadet.jpg", CADET, focus=(0.5, 0.0), top=900),
                      "badge": "PHILIPPINE AIR FORCE", "headline": "*Mary Grace Baloyo*", "headline_size": 96,
                      "sub": "a.k.a. *Amazing Grace* · Bacolod City"},
      "Siya si Mary Grace Baloyo. Pero sa mga kasamahan niya, siya si Amazing Grace.",
      "Lumaki siya sa Bacolod, panganay sa apat na magkakapatid.",
      "Nang sabihin niyang gusto niyang maging piloto, ayaw pa noong una ng nanay niya. Pero kalaunan, pumayag din siya.",
      ("At noong 1997, nagtapos siya sa Philippine Air Force Flying School.",
       "At noong nineteen ninety-seven, nagtapos siya sa Philippine Air Force Flying School."), chapter=True)
scene("03_bronco", {**full("ov10_rain_front.jpg", BRONCO, top=900),
                    "badge": "AT ALAM MO RIN BA?", "headline": "1 sa 6 na babaeng *combat pilot*",
                    "headline_size": 80, "sub": "Piloto ng OV-10 Bronco"},
      "At alam mo rin ba? Hindi lang siya basta piloto. Isa siya sa anim na babaeng combat pilot ng Air Force.",
      ("Ang pinalilipad niya: ang OV-10 Bronco, isang attack plane.",
       "Ang pinalilipad niya: ang O-V ten Bronco, isang attack plane."),
      "Lumipad siya sa kampanya sa Mindanao, sa Camp Abubakar, at ilang medalya na ang natanggap niya.",
      "Bente-siyete anyos pa lang siya noon, at nakatakda na sanang ikasal sa susunod na taon, sa kapwa niya piloto.",
      chapter=True)

# ---------------------------------------------------------------- THE FLIGHT
scene("04_lipad", {**full("ov10_rain_pair.jpg", BRONCO, focus=(0.42, 0.5), top=900),
                   "badge": "MARCH 26, 2001", "headline": "Biglang *nasira* ang makina",
                   "sub": "Cavite hanggang Clark, Pampanga"},
      ("Pero noong March 26, 2001, lumipad siya mula Cavite papuntang Clark, kasama ang co-pilot niyang si Captain Ben Nasayao.",
       "Pero noong March twenty-six, two thousand one, lumipad siya mula Kabite papuntang Clark, kasama ang co-pilot niyang si Captain Ben Nasayao."),
      "Palapit na sila sa Clark, nang biglang masira ang makina.",
      "Mabilis na bumabagsak ang eroplano, papunta sa isang matataong lugar sa Mabalacat, Pampanga.",
      sfx=BOOM, transition="fade")
scene("05_eject", {"badge": "PERO SI GRACE?", "big": "EJECT!", "big_size": 220,
                   "headline": "Si Grace, *hindi* nag-eject",
                   "sub": "Iniwas niya ang eroplano sa mga bahay"},
      "Ilang segundo bago bumagsak, nag-eject si Captain Nasayao. Nakaligtas siya.",
      "Pero si Grace? Hindi siya nag-eject.",
      sfx={**BOOM, "volume": 0.14}, transition="fade")
scene("05b_crash_site", {**full("crash_site.jpg", CRASH, focus=(0.4, 0.5), top=900),
                         "badge": "BARANGAY MABIGA, MABALACAT", "headline": "Ang mismong *crash site*",
                         "headline_size": 80},
      "Nanatili siya sa loob, at pinilit niyang ilayo ang eroplano sa mga bahay.",
      "Bumagsak ito sa isang bakanteng lote, sa gitna ng isang subdivision. Namatay agad si Grace.",
      sfx={**BOOM, "volume": 0.1})

# ---------------------------------------------------------------- AFTER
scene("06_200", {"badge": "AYON SA KANYANG AMA", "big": "200+",
                 "headline": "pamilya ang *nailigtas* niya", "sub": "Barangay Mabiga, Mabalacat"},
      "Ayon sa tatay niya, mahigit dalawandaang pamilya ang nailigtas ng desisyong iyon.",
      "Sabi nga ng isang residente doon: namatay siya, para mabuhay kami.", chapter=True)
scene("07_medal", {**framed("baloyo_cadet.jpg", CADET, photo_w=700),
                   "badge": "MEDAL OF VALOR, 2001", "headline": "Kauna-unahang *babae* sa AFP",
                   "sub": "Iginawad ni Pres. Arroyo", "headline_size": 76},
      "Ginawaran siya ni Pangulong Gloria Macapagal-Arroyo ng Medal of Valor, at itinaas ang ranggo niya bilang Captain.",
      "At heto pa! Siya ang kauna-unahang babae sa buong Armed Forces of the Philippines na nakatanggap nito.",
      "Ayon nga sa isang pahayagan noon: ipinakita niya sa ating lahat kung paano mamatay ang isang tunay na sundalo.",
      sfx=DING, chapter=True)
scene("07b_rebulto", {**full("rebulto_bacolod_bg.jpg", STATUE, focus=(0.1, 0.0), top=900),
                      "badge": "BACOLOD CITY", "headline": "Ang kanyang *rebulto*", "headline_size": 80},
      "Ngayon, may rebulto siya sa Bacolod, para hindi makalimutan ang kabayanihan niya.",
      sfx=POP, transition="fade")

# ---------------------------------------------------------------- ENDING
scene("08_follow", {"badge": "SALAMAT SA PANONOOD!", "headline": "I-share ang kwento ni *Amazing Grace*",
                    "sub": "I-follow ang Juan Fact a Day!", "mascot": True},
      "I-share mo ito, para mas maraming Pilipino ang makakilala kay Amazing Grace.",
      ("At kung gusto mo pa ng mga ganitong kwento, i-follow mo ako, dito sa Juan Fact a Day!",
       "At kung gusto mo pa ng mga ganitong kwento, i-follow mo ako, dito sa Hu-wan Fact a Day!"),
      sfx=POP, hold_after=0.8)

# voice fixes: a clear stop before the 1997 line (it ran into "napapayag din")
for sc in scenes:
    for ln in sc["lines"]:
        if ln["text"].endswith("pumayag din siya."):
            ln["pause_after"] = 0.45

# "pilot" (English) was read the Tagalog way ("pee-lot"); spell it for the voice only, subtitles unchanged
SAY_FIX = {"combat pilot": "kombat pay-lot", "co-pilot": "ko-pay-lot"}
for sc in scenes:
    for ln in sc["lines"]:
        say = ln.get("say", ln["text"])
        for k, v in SAY_FIX.items():
            say = say.replace(k, v)
        if say != ln["text"]:
            ln["say"] = say

story = {
    "extends": "../../channel.json",
    "title": "Mary Grace Baloyo",
    "output": {"path": "004_mary_grace_baloyo.mp4", "aspect": "9:16"},
    "voices": {"narrator": {"engine": "edge", "voice": "fil-PH-AngeloNeural", "rate": "+5%", "pitch": "-2Hz"}},
    "defaults": {"max_pause": 0.35},
    "music": {"path": "audio/score.wav", "volume": 0.13},  # original score: audio/make_score.py
    "scenes": scenes,
    # no "thumbnail" block: the cover is drawn by make_cover.py
}
(HERE / "story.json").write_text(json.dumps(story, ensure_ascii=False, indent=1), encoding="utf-8")
print(len(scenes), "scenes,", sum(len(l["text"].split()) for s in scenes for l in s["lines"]), "words")
