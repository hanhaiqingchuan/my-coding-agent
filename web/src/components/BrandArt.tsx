/**
 * The product's name as block pixel art. Each glyph is a five-row block map
 * rendered as SVG rectangles, so the banner stays aligned and scalable on every
 * platform — it never depends on whether the OS ships a monospace font that
 * covers the block-elements range (the previous ``█`` text art skewed and grew
 * a scrollbar once a fallback font sized the blocks differently).
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

function artRows(text: string): string[] {
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
  return rows;
}

/** The banner is decorative; the name is already present as real text nearby. */
export function BrandArt() {
  const lines = [...artRows("MAKE CODE"), "", ...artRows("GREAT AGAIN")];
  const width = Math.max(...lines.map((line) => line.length));
  const height = lines.length;
  const cells: Array<{ x: number; y: number }> = [];
  lines.forEach((line, y) => {
    for (let x = 0; x < line.length; x += 1) {
      if (line[x] === "█") cells.push({ x, y });
    }
  });
  return (
    <svg
      className="brand-art"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-hidden="true"
      preserveAspectRatio="xMidYMid meet"
    >
      {cells.map((cell, index) => (
        <rect key={index} x={cell.x} y={cell.y} width={1} height={1} />
      ))}
    </svg>
  );
}
