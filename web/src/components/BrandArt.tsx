/**
 * The product's name as ANSI art. Built from a five-row block glyph map so the
 * banner stays aligned at any size; rendered only in the empty conversation.
 */
const GLYPHS: Record<string, string[]> = {
  M: ["█   █", "██ ██", "█ █ █", "█   █", "█   █"],
  A: [" ███ ", "█   █", "█████", "█   █", "█   █"],
  K: ["█  █ ", "█ █  ", "██   ", "█ █  ", "█  █ "],
  E: ["████", "█   ", "███ ", "█   ", "████"],
  C: [" ███", "█   ", "█   ", "█   ", " ███"],
  O: [" ██ ", "█  █", "█  █", "█  █", " ██ "],
  D: ["███ ", "█  █", "█  █", "█  █", "███ "],
  G: [" ███", "█   ", "█ ██", "█  █", " ███"],
  R: ["███ ", "█  █", "███ ", "█ █ ", "█  █"],
  T: ["█████", "  █  ", "  █  ", "  █  ", "  █  "],
  I: ["███", " █ ", " █ ", " █ ", "███"],
  N: ["█   █", "██  █", "█ █ █", "█  ██", "█   █"],
};

function artLines(text: string): string[] {
  const rows = ["", "", "", "", ""];
  for (const char of text) {
    if (char === " ") {
      for (let row = 0; row < 5; row += 1) rows[row] += "   ";
      continue;
    }
    const glyph = GLYPHS[char];
    if (glyph === undefined) continue;
    for (let row = 0; row < 5; row += 1) rows[row] += `${glyph[row]} `;
  }
  return rows.map((row) => row.replace(/ +$/, ""));
}

/** The banner is decorative; the name is already present as real text nearby. */
export function BrandArt() {
  const lines = [...artLines("MAKE CODE"), "", ...artLines("GREAT AGAIN")];
  return (
    <pre className="brand-art" aria-hidden="true">
      {lines.join("\n")}
    </pre>
  );
}
