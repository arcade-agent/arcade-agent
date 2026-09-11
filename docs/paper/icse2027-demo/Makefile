# ICSE 2027 tool demonstration paper.
#
# Requires a LaTeX toolchain with IEEEtran.cls. On Debian/Ubuntu:
#   sudo apt-get install texlive-latex-recommended texlive-publishers \
#                        texlive-science latexmk
# On macOS: MacTeX, or `brew install --cask mactex-no-gui`.
# IEEEtran.cls also ships standalone from CTAN: https://ctan.org/pkg/ieeetran

PAPER = paper

.PHONY: all clean pages check submission

all: $(PAPER).pdf

$(PAPER).pdf: $(PAPER).tex refs.bib
	latexmk -pdf -halt-on-error $(PAPER).tex

# Page count. The track limit is 4 pages INCLUSIVE of references -- verify that
# number against the CFP. Draft mode inflates the count with TODO markers, so
# only `make submission` gives a real answer.
pages: $(PAPER).pdf
	@pdfinfo $(PAPER).pdf | awk '/^Pages:/ {print "Pages: " $$2 "   (limit: 4, inclusive of references)"}'

# Build with the draft scaffolding off, then report the real page count.
submission:
	@sed -i.bak 's/^\\draftmodetrue/\\draftmodefalse/' $(PAPER).tex
	@latexmk -pdf -halt-on-error $(PAPER).tex || true
	@mv $(PAPER).tex.bak $(PAPER).tex
	@pdfinfo $(PAPER).pdf | awk '/^Pages:/ {print "SUBMISSION page count: " $$2 "   (limit: 4)"}'

# Nothing unfinished may ship. Run this before every submission attempt.
check:
	@echo "== TODO markers in the paper =="
	@grep -c '\\todo{' $(PAPER).tex || true
	@echo "== unverified bib entries (STUB) =="
	@grep -c 'STUB' refs.bib || true
	@echo "== placeholder strings that must not survive =="
	@grep -n 'TODO-video-url\|TODO-agent-context\|PLACEHOLDER' $(PAPER).tex refs.bib || echo "  none"

clean:
	latexmk -C
	rm -f $(PAPER).bbl $(PAPER).tex.bak
