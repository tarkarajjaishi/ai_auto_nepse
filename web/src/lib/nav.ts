import {
  Activity,
  ArrowLeftRight,
  BarChart3,
  Boxes,
  CandlestickChart,
  ClipboardList,
  Crosshair,
  FlaskConical,
  Gauge,
  Layers,
  LineChart,
  Radar,
  ScrollText,
  Sparkles,
  Timer,
  Users,
  type LucideIcon,
} from "lucide-react";

export type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  /** the board this screen reads, so the sidebar can show a staleness dot without each page
   *  having to report upward */
  board?: string;
  hint: string;
};

export type NavGroup = { group: string; items: NavItem[] };

/**
 * The sidebar, grouped by what you are actually doing rather than by which script wrote the
 * file. The Streamlit app listed fifteen pages flat and alphabetical-ish, so "Backtest" sat
 * between "Master signal" and "NAASA" and you had to know the app to find anything.
 *
 * Every href here MUST have a route. In Streamlit a renamed page with no matching body drew a
 * blank screen and raised nothing; the Next.js equivalent is a 404, which is at least loud —
 * but the test in `nav.test.ts` pins the pairing anyway.
 */
export const NAV: NavGroup[] = [
  {
    group: "Market",
    items: [
      { href: "/admin/overview", label: "Overview", icon: Gauge, hint: "Everything at a glance" },
      {
        href: "/admin/chart",
        label: "Chart",
        icon: CandlestickChart,
        hint: "Candles, adjusted for corporate actions",
      },
      {
        href: "/admin/heatmap",
        label: "Heatmap",
        icon: Boxes,
        hint: "Sectors and indices by move",
      },
      {
        href: "/admin/floorsheet",
        label: "Floorsheet",
        icon: ScrollText,
        hint: "Broker-level trades, session by session",
      },
    ],
  },
  {
    group: "Signals",
    items: [
      {
        href: "/admin/swing-pro",
        label: "Swing Trader Pro",
        icon: Sparkles,
        board: "swing_pro",
        hint: "The 22-section 1D framework",
      },
      {
        href: "/admin/scanner",
        label: "Scanner",
        icon: Radar,
        board: "scan",
        hint: "Your indicator settings across the market",
      },
      {
        href: "/admin/supply-demand",
        label: "Supply Demand",
        icon: Layers,
        board: "supply_demand",
        hint: "Zones at the last traded candle",
      },
      {
        href: "/admin/master-signal",
        label: "Master signal",
        icon: Crosshair,
        board: "master_signal",
        hint: "Volume-confirmed continuation",
      },
      {
        href: "/admin/swing-master",
        label: "Swing master",
        icon: ClipboardList,
        board: "swing_master",
        hint: "Screen, size, and the exit plan",
      },
    ],
  },
  {
    group: "Flow",
    items: [
      {
        href: "/admin/broker-flow",
        label: "Broker flow",
        icon: ArrowLeftRight,
        hint: "Who accumulated or distributed, over a window",
      },
      {
        href: "/admin/volume-spike",
        label: "Volume spike",
        icon: Activity,
        board: "volume_spike",
        hint: "Unusual volume and who was on each side",
      },
      {
        href: "/admin/operator-radar",
        label: "Operator radar",
        icon: LineChart,
        board: "operator_scan",
        hint: "One broker accumulating a low float",
      },
      {
        href: "/admin/master-operator",
        label: "Master operator",
        icon: Users,
        board: "operator_verdict",
        hint: "Floorsheet only, four windows",
      },
    ],
  },
  {
    group: "Evidence",
    items: [
      {
        href: "/admin/backtest",
        label: "Backtest",
        icon: FlaskConical,
        board: "backtest",
        hint: "What actually survives out of sample",
      },
      { href: "/admin/cron", label: "Pipeline", icon: Timer, hint: "Freshness and rebuilds" },
      {
        href: "/admin/account",
        label: "Account",
        icon: BarChart3,
        hint: "NAASA holdings and orders",
      },
    ],
  },
];

export const ALL_NAV_ITEMS: NavItem[] = NAV.flatMap((g) => g.items);
