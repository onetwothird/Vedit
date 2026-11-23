import { useId } from "react";

/* The vedit mark: a "v" made of two clips on the timeline, split by a cut down the middle. The left clip is ink,
   the right one green (the playhead's colour). The same drawing is public/favicon.svg and the app icon
   (src-tauri/icons/icon-source.svg; regenerate the icon files with `npx tauri icon` from a 1024px render of it). */

const LEFT = [7.1, 6.2] as const; // top of each arm (stroke centre)
const RIGHT = [16.9, 6.2] as const;
const POINT = [12, 18.6] as const; // where the arms meet
const WIDTH = 4.4;
const CUT = 1.1; // the gap between the two clips

const arm = (from: readonly [number, number]) => `M${from[0]} ${from[1]} ${POINT[0]} ${POINT[1]}`;

export default function Logo({ size = 24, className }: { size?: number; className?: string }) {
  const id = useId().replace(/:/g, "");
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <defs>
        <clipPath id={`${id}l`}><rect width={12 - CUT / 2} height="24" /></clipPath>
        <clipPath id={`${id}r`}><rect x={12 + CUT / 2} width={12 - CUT / 2} height="24" /></clipPath>
      </defs>
      <rect width="24" height="24" rx="5.4" fill="#141518" />
      <rect x="0.5" y="0.5" width="23" height="23" rx="4.9" fill="none" stroke="#ffffff1f" />
      <path d={arm(LEFT)} stroke="#ececea" strokeWidth={WIDTH} strokeLinecap="round" fill="none" clipPath={`url(#${id}l)`} />
      <path d={arm(RIGHT)} stroke="#4ade80" strokeWidth={WIDTH} strokeLinecap="round" fill="none" clipPath={`url(#${id}r)`} />
    </svg>
  );
}
