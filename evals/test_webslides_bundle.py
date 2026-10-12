"""A WEB SLIDES DOWNLOAD IS A BUILT REACT BUNDLE — every way it declares a slide is read.

    python -m evals.test_webslides_bundle      # no API key, no network, ~1 second

The reviewer's report: "read 16 slides from session 3, but it has many more". The deck
was built as `fM=[...opening, ...sectionA, ...sectionB]` with one array per section and
divider/screenshot slides produced by factory calls — the first parser read the main
array literally and saw only the opening. Pinned here on a synthetic bundle so the
shapes stay covered without the 4 MB download; the real files are checked too when they
are present in ~/Downloads.
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import webslides   # noqa: E402

OK = FAIL = 0


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


BUNDLE = r'''<!doctype html><html><head><title>Virtualization &amp; Hypervisors</title></head>
<body><div id="root"></div><script type="module">
const ta=[{title:"Virtualization Basics",sub:"Physical vs virtual"},{title:"Hypervisors",sub:"Type 1 and 2"}],
si=ta[1].title,Ct="Virtual Machine Anatomy",
$l=(n,s)=>({id:`${s}-divider`,section:ta[n].title,label:"Section divider",element:i.jsx(y2,{index:n+1}),notes:`TR section ${n+1}.`}),
Fa=(id,sec,k,t,l,img)=>({id,section:sec,label:l,element:i.jsx(we,{title:t,children:i.jsx(Img,img)})}),
aM=[{id:"cc3-brand",section:"Opening",label:"Brand",element:i.jsx(w6,{greeting:"Welcome!"}),notes:"The opening mark."},
    {id:"cc3-agenda",section:"Opening",label:"Agenda",element:i.jsx(j8,{title:"What We Will Cover",items:ta.map(i=>({title:i.title}))})}],
oM=[$l(1,"cc3-s2"),{id:"cc3-vmm",section:si,label:"Hypervisor and VMM",element:i.jsx(we,{kicker:"Hypervisors",title:"Role And Function",lead:"A hypervisor, also called a virtual machine monitor, shares one machine between guests.",points:["Type 1 runs directly on the hardware","Type 2 runs on a host operating system"]}),notes:`Say the everyday version first.`}],
uM=[$l(0,"cc3-s1"),Fa("cc3-install-2",Ct,De.install,"Screenshot Walkthrough","Installing VirtualBox: welcome and licence",{n:2,src:"data:image/png;base64,AAAA",caption:"Welcome screen: click Next"})],
fM=[...aM,...oM,...uM,{id:"cc3-thanks",section:"Closing",label:"Thank you",element:i.jsx(T6,{line:"All the Best"}),notes:'The TR closes on "Thank You | All the Best".'}];
const icons=[{id:"pen",name:"Pen"},{id:"marker",name:"Marker"}];
</script></body></html>'''

h, deck = webslides.extract_upload("virtualization-and-hypervisors.html", BUNDLE.encode(), 3, "S3")
titles = [s["title"] for s in deck["slides"]]
print("\n== every declaration shape is read ==")
check("the deck is the spread-together array, not the first `[{id:` found",
      deck["n_slides"] == 7, str(titles))
check("the page <title> names the deck", deck["deck_title"] == "Virtualization & Hypervisors", deck["deck_title"])
check("a divider built by a factory call is named from the agenda",
      "Section: Hypervisors" in titles and "Section: Virtualization Basics" in titles, str(titles))
check("a section given as a constant resolves (si=ta[1].title)",
      next(s for s in deck["slides"] if s["title"] == "Role And Function")["section"] == "Hypervisors")
vmm = next(s for s in deck["slides"] if s["title"] == "Role And Function")
check("keyed text (lead, points) is the slide body and the notes are kept",
      "virtual machine monitor" in vmm["body"] and "Type 1 runs directly" in vmm["body"]
      and vmm["notes"].startswith("Say the everyday"), vmm["body"][:120])
fa = next((s for s in deck["slides"] if s["title"] == "Screenshot Walkthrough"), None)
check("a slide built by a positional factory call is read", fa is not None, str(titles))
check("…its section comes from the string constant (Ct)", fa and fa["section"] == "Virtual Machine Anatomy", str(fa))
check("…its positional text and the nested caption are the body",
      fa and "Installing VirtualBox" in fa["body"] and "Welcome screen" in fa["body"], str(fa))
check("…and the data: URI is not", fa and "data:" not in fa["body"])
check("the id string is not mistaken for the title", fa and fa["title"] != "cc3-install-2")
check("the icon list is not mistaken for the deck", not any("Pen" in t for t in titles))

print("\n== the real downloads, when present ==")
for f, want in (("cloud-computing-fundamentals.html", 60), ("virtualization-and-hypervisors.html", 62)):
    p = Path.home() / "Downloads" / f
    if not p.exists():
        print(f"  skip {f} (not in ~/Downloads)")
        continue
    _, d = webslides.extract_upload(f, p.read_bytes(), 1, "S")
    secs = []
    for s in d["slides"]:
        if s["section"] not in secs:
            secs.append(s["section"])
    check(f"{f}: {want} slides", d["n_slides"] == want, str(d["n_slides"]))
    check(f"{f}: every slide has a section", all(s["section"] for s in d["slides"]), str(secs))

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
