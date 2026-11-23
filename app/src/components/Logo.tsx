/* The vedit mark: a pixel "v" (same grid as the Geist Pixel wordmark) whose lower half is shifted
   half a pixel to the right, like a jump cut. The same drawing is public/favicon.svg and the app icon. */

const TOP: [number, number][] = [[0, 0], [6, 0], [0, 1], [6, 1], [1, 2], [5, 2]];
const BOTTOM: [number, number][] = [[1, 3], [5, 3], [2, 4], [4, 4], [3, 5]];
const X0 = 2.25;
const Y0 = 2.72;
const CUT = 0.56;
const SHIFT = 0.5;

export const LOGO_PIXELS = [
  ...TOP.map(([c, r]) => [X0 + c, Y0 + r] as const),
  ...BOTTOM.map(([c, r]) => [X0 + c + SHIFT, Y0 + r + CUT] as const),
];

export default function Logo({ size = 24, className }: { size?: number; className?: string }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 12 12" aria-hidden="true">
      <rect width="12" height="12" rx="2.7" fill="#141518" />
      <rect x="0.25" y="0.25" width="11.5" height="11.5" rx="2.5" fill="none" stroke="#ffffff1f" strokeWidth="0.5" />
      {LOGO_PIXELS.map(([x, y]) => (
        <rect key={`${x}-${y}`} x={x + 0.06} y={y + 0.06} width="0.88" height="0.88" rx="0.12" fill="#4ade80" />
      ))}
    </svg>
  );
}
