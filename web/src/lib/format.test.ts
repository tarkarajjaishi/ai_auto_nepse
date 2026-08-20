import { describe, expect, it } from "vitest";

import { decisionTone, gradeTone, volumeNote } from "./format";

describe("gradeTone", () => {
  /**
   * The regression this exists for: the Streamlit board tinted any grade starting with "A"
   * green, so AVOID rendered in the success chip, visually identical to "A+ ELITE".
   */
  it("does not treat AVOID as an A grade", () => {
    expect(gradeTone("AVOID")).toBe("down");
    expect(gradeTone("A+ ELITE")).toBe("up");
    expect(gradeTone("A STRONG")).toBe("up");
  });

  it("keeps B and C distinct", () => {
    expect(gradeTone("B WATCHLIST")).toBe("warn");
    expect(gradeTone("C WEAK")).toBe("flat");
  });
});

describe("decisionTone", () => {
  it("colours the buy family up and the avoid family down", () => {
    expect(decisionTone("BUY")).toBe("up");
    expect(decisionTone("BUY ON RETEST")).toBe("up");
    expect(decisionTone("AVOID")).toBe("down");
    expect(decisionTone("TREND BREAKDOWN")).toBe("down");
  });

  it("treats WAIT as a caution, not a failure", () => {
    expect(decisionTone("WAIT")).toBe("warn");
  });
});

describe("volumeNote", () => {
  /**
   * 0.00x is not "dry" — it means the instrument barely traded, which is a liquidity fact and
   * the opposite of the healthy reading. This shipped wrong once on thin debentures.
   */
  it("calls almost-no-volume thin rather than dry", () => {
    expect(volumeNote(0).text).toContain("barely traded");
    expect(volumeNote(0.04).tone).toBe("flat");
  });

  it("still grades real volume on the spec's wording", () => {
    expect(volumeNote(2.5).text).toContain("very strong");
    expect(volumeNote(1.6).text).toContain("strong");
    expect(volumeNote(0.8).text).toContain("below avg");
  });
});
