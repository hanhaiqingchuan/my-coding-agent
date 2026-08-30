/**
 * Shared motion vocabulary. One spring, one entrance: everything on the desk
 * rises softly and settles; nothing bounces twice. `MotionConfig` in main.tsx
 * honours the OS reduced-motion setting, which turns these into instant cuts.
 */
import type { Variants } from "motion/react";

export const springSoft = {
  type: "spring" as const,
  stiffness: 380,
  damping: 32,
  mass: 0.9,
};

/** Timeline items, banners, chips: enter from slightly below, fade in. */
export const riseIn: Variants = {
  initial: { opacity: 0, y: 10 },
  animate: { opacity: 1, y: 0, transition: springSoft },
  exit: { opacity: 0, y: -6, transition: { duration: 0.14 } },
};

/** The approval gate: a touch more travel, it is the moment that blocks a run. */
export const gateRise: Variants = {
  initial: { opacity: 0, y: 18, scale: 0.985 },
  animate: { opacity: 1, y: 0, scale: 1, transition: springSoft },
  exit: { opacity: 0, y: 8, scale: 0.995, transition: { duration: 0.15 } },
};

/** Menus pop from their anchor. */
export const menuPop: Variants = {
  initial: { opacity: 0, scale: 0.96, y: 4 },
  animate: { opacity: 1, scale: 1, y: 0, transition: { duration: 0.16 } },
  exit: { opacity: 0, scale: 0.97, transition: { duration: 0.1 } },
};

/** List stagger: the parent orchestrates, children rise. */
export const staggerParent: Variants = {
  initial: {},
  animate: { transition: { staggerChildren: 0.045 } },
};
export const staggerChild: Variants = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0, transition: springSoft },
};
