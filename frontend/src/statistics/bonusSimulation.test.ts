import { describe, expect, it } from "vitest";

import { BONUS_SLIDER_STEPS, sliderPositionFor, trimTrailingZeros } from "./bonusSimulation";

describe("trimTrailingZeros", () => {
  it("leaves an integer with no fraction unchanged", () => {
    expect(trimTrailingZeros("5")).toBe("5");
  });

  it("strips trailing fractional zeros, then a trailing dot", () => {
    expect(trimTrailingZeros("5.00")).toBe("5");
    expect(trimTrailingZeros("1.50")).toBe("1.5");
    expect(trimTrailingZeros("0.10")).toBe("0.1");
  });

  it("leaves a fraction with no trailing zero unchanged", () => {
    expect(trimTrailingZeros("3.25")).toBe("3.25");
  });
});

describe("sliderPositionFor", () => {
  it("accepts a value already on the slider's grid, comma or dot", () => {
    expect(sliderPositionFor("3,5")).toBe("3.5");
    expect(sliderPositionFor("3.5")).toBe("3.5");
    expect(sliderPositionFor("10")).toBe("10");
    expect(sliderPositionFor("0")).toBe("0");
  });

  it("matches after trimming trailing zeros ('5.00' is slider stop '5')", () => {
    expect(sliderPositionFor("5.00")).toBe("5");
  });

  it("is null for a value outside the slider's 0-10 range (the input field overrides the slider)", () => {
    expect(sliderPositionFor("25")).toBeNull();
    expect(sliderPositionFor("-1")).toBeNull();
  });

  it("is null for a value off the slider's 0.5 grid", () => {
    expect(sliderPositionFor("3.25")).toBeNull();
  });

  it("is null for unparseable text", () => {
    expect(sliderPositionFor("abc")).toBeNull();
    expect(sliderPositionFor("")).toBeNull();
  });

  it("every step is a member of BONUS_SLIDER_STEPS by construction", () => {
    for (const step of BONUS_SLIDER_STEPS) {
      expect(sliderPositionFor(step)).toBe(step);
    }
  });
});
