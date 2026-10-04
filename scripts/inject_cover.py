#!/usr/bin/env python3
from bs4 import BeautifulSoup
from pathlib import Path
import sys

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
soup = BeautifulSoup(src.read_text(encoding="utf-8"), "html.parser")

style = soup.find("style")
style.append("""
@page coverpage { size: A4; margin: 0; }
.cover-image-page { page: coverpage; width:210mm; height:297mm; margin:0; padding:0; page-break-after:always; overflow:hidden; }
.cover-image-page img { display:block; width:210mm; height:297mm; object-fit:cover; }
""")

old = soup.find("section", class_="cover")
new = soup.new_tag("section", attrs={"class": "cover-image-page"})
img = soup.new_tag("img", src="../assets/cover.png", alt="AX & Agent Infrastructure")
new.append(img)
if old:
    old.replace_with(new)
else:
    soup.body.insert(0, new)

dst.write_text(str(soup), encoding="utf-8")
