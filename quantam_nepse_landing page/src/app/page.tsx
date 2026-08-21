import type { CSSProperties, ReactNode } from "react";
import {
  ChartColumn,
  ClipboardList,
  EllipsisVertical,
  Landmark,
  Newspaper,
  ShieldCheck,
  TrendingUp,
  Users,
} from "lucide-react";
import { Reveal } from "./reveal";

/* Numbers below are artboard px (1280x720). 1rem == 1 artboard px, see globals.css.
   Everything lives on ONE screen — the page never scrolls, so any element added
   here has to be budgeted against the 1280x720 box, not appended below it. */
const S = (o: Record<string, number | string>): CSSProperties =>
  Object.fromEntries(
    Object.entries(o).map(([k, v]) => [k, typeof v === "number" ? `${v}rem` : v]),
  ) as CSSProperties;

const LABEL = "#9ccbb4";
const VALUE = "#e6f3ec";
const TITLE = "#d6ece1";

/* Three ragged columns. Every card carries its OWN x and w — a rigid grid of
   eight identical rectangles reads as a table, not as a system, so the widths
   and left edges vary by up to 26px. Nothing is eyeballed against those edges:
   every connector below is derived from rt()/mid(), so moving a card moves its
   wiring with it. The centre column stays centred on 640, the artboard's true
   axis, because the drop into Order Execution has to land on its centreline. */
const BOX = {
  tech: { x: 30, y: 100, w: 318, h: 133 },
  fund: { x: 44, y: 245, w: 292, h: 133 },
  news: { x: 36, y: 390, w: 306, h: 114 },
  churn: { x: 52, y: 516, w: 128, h: 144 },
  brok: { x: 192, y: 516, w: 136, h: 144 },
  hub: { x: 545, y: 100, w: 190, h: 116 },
  exec: { x: 482, y: 340, w: 316, h: 320 },
  risk: { x: 952, y: 124, w: 292, h: 172 },
  sent: { x: 930, y: 324, w: 318, h: 190 },
  score: { x: 1092, y: 542, w: 156, h: 118 },
};

type Box = { x: number; y: number; w: number; h: number };
const mid = (b: Box) => b.y + b.h / 2;
const rt = (b: Box) => b.x + b.w;

const FEED = mid(BOX.tech); /* the lane every left-hand analyst merges onto */
const RAIL = 410; /* the collector between the left column and the centre */
const OUT = 850; /* where the decision's run branches toward Risk Filter */
const SPINE = 14; /* the market-data feed running down outside the analyst stack */
/* Sentiment hands its score down to the tile that shows it. The two panels are
   only 28px apart, so a straight drop would be shorter than its own arrowhead —
   the run tucks into the lane between them and crosses to the tile's axis. */
const SCORE_TAP = 1000;
const SCORE_LANE = 528;
const SRC = 150; /* where that feed enters the page */
/* THREE runs between the exchange and the decision: two plain dashed arrows on
   the flanks, and a centre run that carries the symbols. The boxes ride the
   centre line, stacked — the flanking arrows stay bare. All three sit inside the
   exchange box above (545..735) and the decision card below (482..798). */
const PIPE = [594, 686];
const CENTRE = 640;

const GAP = 8; /* how far a run stands off the cards at BOTH ends: a drifting
                  card moves +-3px, and a run touching its edge would spend part
                  of every cycle inside it */

function Card({
  x,
  y,
  w,
  h,
  className = "",
  style,
  children,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  className?: string;
  style?: CSSProperties;
  children: ReactNode;
}) {
  return (
    <div
      className={`glass absolute overflow-hidden ${className}`}
      style={{ ...S({ left: x, top: y, width: w, height: h }), ...style }}
    >
      {children}
    </div>
  );
}

function Head({
  icon: Icon,
  title,
  color = TITLE,
  top = 17,
}: {
  icon: typeof ChartColumn;
  title: string;
  color?: string;
  top?: number;
}) {
  return (
    <div
      className="absolute flex items-center justify-between"
      style={S({ left: 18, right: 16, top })}
    >
      <span className="flex items-center" style={S({ gap: 8 })}>
        <Icon style={S({ width: 14, height: 14 })} color="#32d583" strokeWidth={1.9} />
        <span style={S({ fontSize: 12.5, color, letterSpacing: 0.1 })}>{title}</span>
      </span>
      <EllipsisVertical
        style={S({ width: 13, height: 13 })}
        color="rgba(255,255,255,0.40)"
        strokeWidth={1.8}
      />
    </div>
  );
}

/* The hairline under a card's title. Separate from Head because Head is a flex
   row and this has to span the card's full inner width, not sit inside it. */
function Rule({ top }: { top: number }) {
  return (
    <div
      className="absolute"
      style={{
        ...S({ left: 18, right: 16, top, height: 1 }),
        background:
          "linear-gradient(90deg,rgba(108,233,166,0.34),rgba(108,233,166,0.10) 60%,transparent)",
      }}
    />
  );
}

function Row({
  k,
  v,
  top,
  dot,
  kColor = LABEL,
  vColor = VALUE,
  size = 11,
}: {
  k: string;
  v: ReactNode;
  top: number;
  dot?: string;
  kColor?: string;
  vColor?: string;
  size?: number;
}) {
  return (
    <div
      className="absolute flex items-center justify-between"
      style={S({ left: 18, right: 18, top, fontSize: size })}
    >
      <span style={{ color: kColor }}>{k}</span>
      <span className="flex items-center" style={{ ...S({ gap: 6 }), color: vColor }}>
        {v}
        {dot ? (
          <i
            className="inline-block rounded-full"
            style={{ ...S({ width: 6, height: 6 }), background: dot }}
          />
        ) : null}
      </span>
    </div>
  );
}

/* The page runs a BUY phase and a SELL phase, flipping on a fixed cycle (see
   `theme` in globals.css). Both readings live in the DOM and each is shown in
   its own half — there is no JS to swap them, and `page.tsx` stays a server
   component.

   The sell copy is absolutely positioned over the buy copy and right-aligned,
   because every place this is used sits on the right of a key/value row. */
function Swap({ buy, sell }: { buy: ReactNode; sell: ReactNode }) {
  return (
    <span className="swap">
      <span className="on-buy">{buy}</span>
      <span className="on-sell">{sell}</span>
    </span>
  );
}

/* A value that cycles through plausible readings. steps() on a clipped strip —
   no JS, so the page stays a server component. The strip MUST be a block box;
   transform is ignored on an inline one, which silently froze every reading. */
function Ticker({
  values,
  dur = 7,
  delay = 0,
}: {
  values: string[];
  dur?: number;
  delay?: number;
}) {
  return (
    <span className="tick">
      <span
        style={
          { "--dur": `${dur}s`, "--n": values.length, "--delay": `${delay}s` } as CSSProperties
        }
      >
        {values.map((v) => (
          <b key={v}>{v}</b>
        ))}
      </span>
    </span>
  );
}

/* Scrolling chart. The path starts and ends at the same y, so two copies laid
   end to end translate by exactly one width and loop seamlessly. */
function Wave({
  w,
  h,
  d,
  id,
  stroke,
  dur,
  from,
}: {
  w: number;
  h: number;
  d: string;
  id: string;
  stroke: string;
  dur: number;
  from: string;
}) {
  return (
    <svg
      aria-hidden="true"
      className="absolute bottom-0 w-full"
      style={S({ height: h })}
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      fill="none"
    >
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={from} stopOpacity="0.55" />
          <stop offset="100%" stopColor={from} stopOpacity="0" />
        </linearGradient>
      </defs>
      <g className="wave" style={{ animationDuration: `${dur}s`, "--w": `${-w}px` } as CSSProperties}>
        {[0, w].map((x) => (
          <g key={x} transform={`translate(${x},0)`}>
            <path d={`${d} L${w},${h} L0,${h} Z`} fill={`url(#${id})`} />
            <path d={d} stroke={stroke} strokeWidth="1.8" vectorEffect="non-scaling-stroke" />
          </g>
        ))}
      </g>
    </svg>
  );
}

/* Brokers Steps — one entry per column, [from, to] of the scaleY breathe. */
const BARS: [number, number][] = [
  [0.35, 0.88],
  [0.62, 0.3],
  [0.48, 0.95],
  [0.8, 0.42],
  [0.55, 0.72],
  [0.9, 0.36],
  [0.42, 0.84],
  [0.7, 0.28],
  [0.58, 0.92],
  [0.85, 0.45],
  [0.5, 0.78],
  [0.75, 0.33],
  [0.4, 0.86],
  [0.66, 0.38],
];

/* The 13 NEPSE sub-indices, named as the board names them — no "Index" or
   "SubIndex" suffix, and Manufacturing And Processing spelled out in full. One
   shows at a time, large, and flips to the next: 13 names at a readable size do
   not fit in the box at once, and a grid of them read as a legend rather than as
   the thing the exchange is the sum of. See .flip in globals.css. */
const SECTORS = [
  "Banking",
  "Development Bank",
  "Finance",
  "Hotels And Tourism",
  "Hydropower",
  "Investment",
  "Life Insurance",
  "Manufacturing And Processing",
  "Microfinance",
  "Mutual Fund",
  "Non-Life Insurance",
  "Others",
  "Trading",
];

/* one full turn of the flip, and the slot each name gets inside it */
const FLIP_S = 1.6;

/* source -> target. The page is one flow and this is it: the four analysts feed
   the exchange, the exchange hands its read down to the decision, and the
   decision — alone — goes out to risk and sentiment.

   The three lower analysts tap a collector rail rather than each drawing its own
   diagonal, which keeps every run orthogonal against four ragged right edges.
   `true` = the run ends in an arrowhead. */
const FLOW: [string, boolean][] = [
  [`M${SPINE},${SRC} V${mid(BOX.churn)}`, false],
  [`M${SPINE},${FEED} H${BOX.tech.x - GAP}`, true],
  [`M${SPINE},${mid(BOX.fund)} H${BOX.fund.x - GAP}`, true],
  [`M${SPINE},${mid(BOX.news)} H${BOX.news.x - GAP}`, true],
  [`M${SPINE},${mid(BOX.churn)} H${BOX.churn.x - GAP}`, true],
  [`M${rt(BOX.tech) + GAP},${FEED} H${BOX.hub.x - GAP}`, true],
  [`M${rt(BOX.fund) + GAP},${mid(BOX.fund)} H${RAIL}`, false],
  [`M${rt(BOX.news) + GAP},${mid(BOX.news)} H${RAIL}`, false],
  [`M${rt(BOX.brok) + GAP},${mid(BOX.brok)} H${RAIL}`, false],
  [`M${RAIL},${mid(BOX.brok)} V${FEED}`, false],
  [`M${PIPE[0]},${BOX.hub.y + BOX.hub.h + GAP} V${BOX.exec.y - GAP}`, true],
  [`M${PIPE[1]},${BOX.hub.y + BOX.hub.h + GAP} V${BOX.exec.y - GAP}`, true],
  [`M${rt(BOX.exec) + GAP},${mid(BOX.sent)} H${BOX.sent.x - GAP}`, true],
  [`M${OUT},${mid(BOX.sent)} V${mid(BOX.risk)} H${BOX.risk.x - GAP}`, false],
  [
    `M${SCORE_TAP},${BOX.sent.y + BOX.sent.h + GAP} V${SCORE_LANE} H${BOX.score.x + BOX.score.w / 2} V${BOX.score.y - GAP}`,
    true,
  ],
];

/* One pulse per source, each travelling its WHOLE route to the target — the
   three rail taps share the rail and the feed lane, which is what makes the
   merge read as a merge rather than three unrelated lines. */
const PULSE = [
  `M${SPINE},${SRC} V${FEED} H${BOX.tech.x - GAP}`,
  `M${SPINE},${SRC} V${mid(BOX.fund)} H${BOX.fund.x - GAP}`,
  `M${SPINE},${SRC} V${mid(BOX.news)} H${BOX.news.x - GAP}`,
  `M${SPINE},${SRC} V${mid(BOX.churn)} H${BOX.churn.x - GAP}`,
  `M${rt(BOX.tech) + GAP},${FEED} H${BOX.hub.x - GAP}`,
  `M${rt(BOX.fund) + GAP},${mid(BOX.fund)} H${RAIL} V${FEED} H${BOX.hub.x - GAP}`,
  `M${rt(BOX.news) + GAP},${mid(BOX.news)} H${RAIL} V${FEED} H${BOX.hub.x - GAP}`,
  `M${rt(BOX.brok) + GAP},${mid(BOX.brok)} H${RAIL} V${FEED} H${BOX.hub.x - GAP}`,
  `M${rt(BOX.exec) + GAP},${mid(BOX.sent)} H${BOX.sent.x - GAP}`,
  `M${rt(BOX.exec) + GAP},${mid(BOX.sent)} H${OUT} V${mid(BOX.risk)} H${BOX.risk.x - GAP}`,
  `M${SCORE_TAP},${BOX.sent.y + BOX.sent.h + GAP} V${SCORE_LANE} H${BOX.score.x + BOX.score.w / 2} V${BOX.score.y - GAP}`,
];

/* where a run taps a card instead of arriving at it — drawn as a ring port */
const PORTS: [number, number][] = [
  [SPINE, SRC],
  [rt(BOX.fund) + GAP, mid(BOX.fund)],
  [rt(BOX.news) + GAP, mid(BOX.news)],
  [rt(BOX.brok) + GAP, mid(BOX.brok)],
  [OUT, mid(BOX.sent)],
  [BOX.risk.x - GAP, mid(BOX.risk)],
  [SCORE_TAP, BOX.sent.y + BOX.sent.h + GAP],
];

/* Every tradable symbol on the board, streaming down the pipe into the decision.
   Symbols only — the full names are unreadable at this size and the point is the
   VOLUME of them, not any one. Rendered as a single strip (see .feed): one
   animated element with static children, not 492 animated elements. */
const SYMBOLS = [
  "ACLBSL", "ACLBSLP", "ADBL", "ADLB", "AHL", "AHPC", "AIL", "AKBSL", "AKBSLP", "AKJCL",
  "AKPL", "ALBSL", "ALBSLP", "ALDBL", "ALICL", "ALICLP", "AMFI", "ANLB", "APHL", "API",
  "AVYAN", "BANDIPUR", "BARUN", "BBC", "BEDC", "BFC", "BFCPO", "BGWT", "BHBL", "BHCL", "BHDC",
  "BHL", "BHPL", "BJHL", "BNHC", "BNL", "BNT", "BOKL", "BOKLPO", "BPCL", "BPW", "BUNGAL",
  "CBBL", "CBBLPO", "CBL", "CBLPO", "CCBL", "CCBLPO", "CEFL", "CFCL", "CFCLPO", "CGH", "CHCL",
  "CHDC", "CHL", "CHLBS", "CIT", "CITPO", "CITY", "CKHL", "CLI", "CORBL", "CREST", "CYCL",
  "CZBIL", "CZBILP", "DBBL", "DDBL", "DDBLPO", "DHEL", "DHPL", "DLBS", "DLBSL", "DOLTI",
  "DORDI", "EBL", "EBLPO", "ECL", "EDBL", "EDBLPO", "EHPL", "EIC", "EICPO", "ENL", "FMDBL",
  "FMDBLP", "FOWAD", "FOWADP", "GBBL", "GBBLPO", "GBIME", "GBIMEP", "GBLBS", "GBLBSP", "GCIL",
  "GDBL", "GFCL", "GFCLPO", "GFL", "GGBSL", "GHL", "GIC", "GILB", "GILBPO", "GLBSL", "GLH",
  "GLICL", "GLICLP", "GMFBS", "GMFIL", "GMFILP", "GMLI", "GRDBL", "GRDBLP", "GUFL", "GUFLPO",
  "GVL", "HAMRO", "HATH", "HATHY", "HBL", "HBLPO", "HDHPC", "HDL", "HEI", "HEIP", "HFIN",
  "HGI", "HHL", "HIDCL", "HIDCLP", "HIMSTAR", "HLBSL", "HLBSLP", "HLI", "HLIPO", "HPPL", "HRL",
  "HURJA", "ICFC", "ICFCPO", "IGI", "IGIPO", "IHL", "ILBS", "ILBSP", "ILI", "JALPA", "JBBL",
  "JBBLPO", "JBLB", "JBLBP", "JBNL", "JEFL", "JFL", "JFLPO", "JHAPA", "JLI", "JOSHI", "JSLBB",
  "JSLBBP", "KADBL", "KAHL", "KBBL", "KBL", "KBLPO", "KBSH", "KDL", "KEBL", "KHPL", "KKHC",
  "KLBS", "KLBSL", "KLBSLP", "KMCDB", "KMCDBP", "KMFL", "KNBL", "KPCL", "KRBL", "KRBLPO",
  "KSBBL", "KSBBLP", "LBBL", "LBBLPO", "LBL", "LBLPO", "LEC", "LFC", "LFLC", "LGIL", "LGILPO",
  "LICN", "LLBS", "LSL", "LSLPO", "MABEL", "MAKAR", "MANDU", "MATRI", "MBJC", "MBL", "MBLPO",
  "MCHL", "MDB", "MDBPO", "MEGA", "MEGAPO", "MEHL", "MEL", "MEN", "MEPDL", "MERO", "MEROPO",
  "MFIL", "MFILPO", "MHCL", "MHL", "MHNL", "MIDBL", "MKCL", "MKHC", "MKHL", "MKJC", "MKLB",
  "MLBBL", "MLBBLP", "MLBL", "MLBLPO", "MLBS", "MLBSL", "MMFDB", "MMFDBP", "MMKJL", "MNBBL",
  "MNBBLP", "MPFL", "MPFLPO", "MSHL", "MSLB", "MSLBP", "MSMBS", "NABBC", "NABBCP", "NABIL",
  "NABILP", "NADEP", "NADEPP", "NAGRO", "NBB", "NBBL", "NBIL", "NBL", "NBSL", "NCCB", "NCCBPO",
  "NCDB", "NESDO", "NFS", "NFSPO", "NGPL", "NHDL", "NHPC", "NIB", "NIBPO", "NICA", "NICAP",
  "NICL", "NICLBSL", "NICLBSLP", "NICLPO", "NIFRA", "NIFRAP", "NIL", "NILPO", "NIMB", "NIMBPO",
  "NLBBL", "NLBBLP", "NLG", "NLIC", "NLICL", "NLICLP", "NLICP", "NLO", "NMB", "NMBMF",
  "NMBMFP", "NMBPO", "NMFBS", "NMFBSP", "NMIC", "NMLBBL", "NNLB", "NRIC", "NRICP", "NRM",
  "NRN", "NSEWA", "NSLB", "NSLBP", "NTC", "NUBL", "NUBLPO", "NWCL", "NYADI", "ODBL", "OHL",
  "OMPL", "PCBL", "PCBLP", "PCIL", "PFL", "PFLPO", "PHCL", "PIC", "PICL", "PICLPO", "PICPO",
  "PLI", "PLIC", "PLICPO", "PMHPL", "PMLI", "PMLIP", "PPCL", "PPL", "PRIN", "PRINPO", "PROFL",
  "PROFLP", "PRVU", "PRVUPO", "PURBL", "PURE", "RADHI", "RAWA", "RBCL", "RBCLPO", "RFPL",
  "RHGCL", "RHPC", "RHPL", "RIDI", "RLEL", "RLFL", "RLFLPO", "RLI", "RMDC", "RMDCPO", "RNLI",
  "RRHP", "RSDC", "RSDCP", "RSML", "RULB", "RURU", "SABBL", "SABSL", "SADBL", "SADBLP",
  "SAGAR", "SAHAS", "SAIL", "SALICO", "SALICOPO", "SAMAJ", "SANIMA", "SANVI", "SAPDBL",
  "SAPDBLP", "SAPIL", "SARBTM", "SBBLJ", "SBI", "SBL", "SBLPO", "SCB", "SDESI", "SDLBSL",
  "SFCL", "SFCLP", "SFFIL", "SGHC", "SGHL", "SGI", "SGIC", "SHEL", "SHINE", "SHINEP", "SHIVM",
  "SHL", "SHLB", "SHPC", "SIC", "SICL", "SICLPO", "SICPO", "SIFC", "SIFCPO", "SIKLES", "SIL",
  "SILPO", "SINDU", "SINDUP", "SIPD", "SJCL", "SJLIC", "SJLICP", "SKBBL", "SKBBLP", "SKHEL",
  "SKHL", "SLBBL", "SLBBLP", "SLBS", "SLBSL", "SLBSP", "SLI", "SLICL", "SLICLP", "SMATA",
  "SMATAP", "SMB", "SMBPO", "SMFBS", "SMFDB", "SMFDBP", "SMH", "SMHL", "SMJC", "SMPDA", "SNLI",
  "SNMAPO", "SNORL", "SOHL", "SONA", "SOPL", "SPARS", "SPC", "SPDL", "SPHL", "SPIL", "SPILPO",
  "SPL", "SRBL", "SRBLPO", "SRLI", "SRS", "SSHL", "STC", "SWASTIK", "SWBBL", "SWBBLP", "SWMF",
  "SWMFPO", "SYFL", "SYPNL", "TAMOR", "TMDBL", "TPC", "TPKHL", "TRH", "TSHL", "TTL", "TVCL",
  "UAIL", "UAILPO", "UFL", "UHEWA", "UIC", "UICPO", "ULBSL", "ULHC", "ULI", "UMHL", "UMRH",
  "UNHPL", "UNL", "UNLB", "UPCL", "UPPER", "USHEC", "USHL", "USLB", "VLBS", "VLBSPO", "VLUCL",
  "WNLB", "WOMI", "WOMIPO", "YMHL",
];

/* The intro line — the whole sentence in one pill. It opens once on load and
   then idles, with .shine sweeping through it. */
/* Split in two so the phone can keep the greeting and drop the rest: the full
   sentence needs three wrapped lines at 366px, which turns a one-line pill into
   a paragraph sitting above the fold. `.intro-rest` is hidden by the mobile
   stylesheet — the text stays in the HTML either way, so nothing is lost to a
   crawler. */
const INTRO_LEAD = "Welcome to NEPSE Quantum";
const INTRO_REST =
  " — a smarter way to experience NEPSE. Live prices, broker flow and AI-scored signals in one view.";

/* A stream of symbols down the centre. PASS is both the travel time AND the
   ticker's step interval, so a label flips to its next symbol at the exact
   moment its box restarts at the top — every pass carries a new symbol.

   LANE_COUNT boxes are in flight at once, each offset by one PASS/LANE_COUNT, so
   they arrive as a conveyor. Each lane owns its OWN slice of the list rather
   than sharing it on a delay: a CSS ticker must have every value in the
   document, so slicing keeps the DOM flat (~492 symbol nodes however many lanes
   there are) and guarantees no two boxes ever show the same symbol. */
const PASS = 1.2;
/* The feed run is a NEWS TICKER, not a set of fading packets: one strip of
   symbols scrolling toward the exchange, clipped at the card's edge so a symbol
   disappears BY reaching it rather than by fading out in mid-air.

   Every 5th symbol keeps the strip long enough not to visibly repeat while
   holding the DOM to ~200 boxes instead of ~1000. */
const TICKER_SYMBOLS = SYMBOLS.filter((_, i) => i % 5 === 0);
const TICKER_SPEED = 34; /* artboard px per second */
const TICKER_BOX = 60 + 16; /* box + gap, for the loop's duration */
/* THREE lanes down the centre, not five. The run is ~90px and each box is 15
   tall with an 11px arrowhead hanging below it, so five lanes put boxes 3px
   apart and the arrowheads straight through the box beneath. Three gives a
   30px pitch: 15 box + 11 arrow + 4 clear. */
const LANE_COUNT = 3;
const LANE_SIZE = Math.ceil(SYMBOLS.length / LANE_COUNT);
const LANES = Array.from({ length: LANE_COUNT }, (_, i) =>
  SYMBOLS.slice(i * LANE_SIZE, (i + 1) * LANE_SIZE),
);


/* The ground plane: concentric ellipses centred just BELOW the artboard's bottom
   edge, so only their upper arcs show and they read as a floor in perspective
   rather than as rings. Opacity falls off outward — the near arcs carry the
   effect, the far ones only suggest it. */
const DOME = [
  { rx: 150, ry: 40, o: 0.2 },
  { rx: 250, ry: 66, o: 0.165 },
  { rx: 355, ry: 92, o: 0.13 },
  { rx: 465, ry: 118, o: 0.1 },
  { rx: 580, ry: 144, o: 0.075 },
  { rx: 700, ry: 170, o: 0.055 },
];

/* One comet per route, stacked from four dashes of the same path: a soft wide
   glow and a faint tail sitting a few units BEHIND the bright head. That offset
   is what --lag buys — one shared keyframe, shifted per layer. */
const COMET = [
  { w: 1.6, dash: 22, o: 0.14, lag: 20 },
  { w: 2.1, dash: 12, o: 0.3, lag: 9 },
  { w: 9, dash: 3, o: 0.18, lag: 0 },
  { w: 3.2, dash: 2.6, o: 1, lag: 0 },
];

export default function Home() {
  return (
    <main className="stage">
      {/* app window — fills the viewport, see .window in globals.css */}
      <div className="window" />

      {/* Ambient motion behind the composition. It paints AFTER .window (which is
          a near-opaque dark sheet) or it would be invisible, and rides at low
          opacity as a texture rather than a picture. autoplay needs `muted` —
          browsers block sound-on autoplay — and `playsInline` stops iOS taking
          it fullscreen. No JS, so page.tsx stays a server component. */}
      <video
        className="bgvid"
        src="/bg_loop.mp4"
        autoPlay
        muted
        loop
        playsInline
        aria-hidden="true"
        tabIndex={-1}
      />

      {/* the ground the composition stands on */}
      <svg
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 h-full w-full"
        viewBox="0 0 1280 720"
        fill="none"
      >
        <defs>
          <radialGradient id="domeGlow">
            <stop offset="0%" stopColor="#32d583" stopOpacity="0.20" />
            <stop offset="60%" stopColor="#12b76a" stopOpacity="0.07" />
            <stop offset="100%" stopColor="#12b76a" stopOpacity="0" />
          </radialGradient>
        </defs>
        <ellipse cx="640" cy="726" rx="620" ry="165" fill="url(#domeGlow)" />
        <g className="dome" stroke="#6ce9a6" strokeWidth="1">
          {DOME.map((d, i) => (
            <ellipse
              key={d.rx}
              cx="640"
              cy="726"
              rx={d.rx}
              ry={d.ry}
              opacity={d.o}
              style={{ "--o": d.o, animationDelay: `-${(i * 1.4).toFixed(2)}s` } as CSSProperties}
            />
          ))}
        </g>
      </svg>

      <Reveal delay={0}>
      {/* nav — `topbar` is the handle the mobile stylesheet needs: reflowed into
          a column it is the one child that must wrap rather than shrink, and
          utility classes give it nothing to select on. */}
      <div
        className="topbar absolute flex items-center justify-between"
        style={S({ left: 60, top: 25, width: 1160, height: 22 })}
      >
        {/* `brand` is the handle the mobile stylesheet scales up — the mark is
            drawn at 16px + 13px type for a 1280px artboard, which on a phone is
            a caption, not a masthead. */}
        <span className="brand flex items-center" style={S({ gap: 6 })}>
          <svg
            aria-hidden="true"
            viewBox="0 0 20 20"
            style={S({ width: 16, height: 16 })}
            fill="none"
          >
            <g className="orbit">
              <ellipse
                cx="10"
                cy="10"
                rx="9"
                ry="4.4"
                transform="rotate(-28 10 10)"
                stroke="#32d583"
                strokeWidth="1.2"
              />
            </g>
            <circle cx="10" cy="10" r="3.4" stroke="#d1fadf" strokeWidth="1.2" />
          </svg>
          <span className="brand-t" style={S({ fontSize: 13, color: "#e1efe9", fontWeight: 400 })}>
            Nepse Quantam
          </span>
        </span>
        {/* Centred on the artboard axis (580 here, since the bar starts at x 60),
            and wide enough for the whole line so it never wraps. */}
        <div
          className="intro shine absolute flex items-center justify-center overflow-hidden whitespace-nowrap"
          style={{
            ...S({
              /* As long as it can be while staying centred on the artboard axis:
                 the bar's middle runs from the brand's edge (~115) to the link
                 group's (~1005), so a centred box maxes out at 2 x 425. */
              left: 197,
              top: 0,
              width: 766,
              height: 22,
              /* a rounded RECTANGLE, not a capsule. On an 18px-tall box the
                 radius has to stay well under half the height or the ends read
                 as a pill: this is 22% of it. */
              borderRadius: 4,
              paddingLeft: 14,
              paddingRight: 14,
              /* sized to the BOX, which shrank to give Blogs its gap: at 12 the
                 121-char line overflowed and the trailing full stop was clipped */
              fontSize: 11,
              letterSpacing: 0.2,
              borderWidth: 1,
            }),
            background: "rgba(18,183,106,0.14)",
            borderStyle: "solid",
            borderColor: "rgba(108,233,166,0.34)",
            color: "#d8f3e6",
            boxSizing: "border-box",
          }}
        >
          <span>{INTRO_LEAD}</span>
          <span className="intro-rest">{INTRO_REST}</span>
        </div>

        <nav className="flex items-center" style={S({ gap: 21, fontSize: 8.5 })}>
          <a
            href="/blogs"
            className="flex items-center"
            style={{
              ...S({ height: 22, paddingLeft: 10, paddingRight: 10, borderRadius: 4, borderWidth: 1 }),
              background: "rgba(18,183,106,0.14)",
              borderStyle: "solid",
              borderColor: "rgba(108,233,166,0.34)",
              /* NEUTRAL, not the palette's mint. The whole document is
                 hue-rotated and saturate(1.9)'d for the sell phase, and that
                 amplifies whatever tint a colour already has — a green-tinted
                 label came out as red text on a red fill. A near-achromatic
                 colour has no hue to amplify, so it reads the same in both
                 phases. Same reason for the button below. */
              color: "#eef2f0",
            }}
          >
            Blogs
          </a>
          <a
            href="#"
            className="flex items-center"
            style={{
              ...S({ height: 22, paddingLeft: 12, paddingRight: 12, borderRadius: 4 }),
              background: "linear-gradient(180deg,#32d583,#12b76a)",
              color: "#0b0d0c",
              boxSizing: "border-box",
              boxShadow: "0 5rem 14rem rgba(18,183,106,0.45)",
            }}
          >
            Request For Access
          </a>
        </nav>
      </div>

      </Reveal>
      <Reveal delay={0.06}>
      {/* The visible hero is gone, but a page still needs ONE heading: without
          it a screen reader has no title for the document. Kept off-screen. */}
      <h1 className="sr-only">Nepse Quantam — AI-powered NEPSE trading workflows</h1>

      </Reveal>
      <Reveal delay={0.42}>
      {/* the flow: analysts -> exchange -> decision -> risk & sentiment */}
      <svg
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 h-full w-full"
        viewBox="0 0 1280 720"
        fill="none"
      >
        <defs>
          <marker
            id="arw"
            viewBox="0 0 10 8"
            refX="9"
            refY="4"
            markerWidth="7"
            markerHeight="5.6"
            orient="auto"
          >
            <path d="M0,0 L10,4 L0,8 Z" fill="#6ce9a6" />
          </marker>
          <radialGradient id="hubGlow">
            <stop offset="0%" stopColor="#32d583" stopOpacity="0.34" />
            <stop offset="55%" stopColor="#12b76a" stopOpacity="0.12" />
            <stop offset="100%" stopColor="#12b76a" stopOpacity="0" />
          </radialGradient>
        </defs>

        <circle cx="640" cy="200" r="115" fill="url(#hubGlow)" />

        <g stroke="#3ddc8c" strokeWidth="1.4" strokeDasharray="5 5" opacity="0.62">
          {FLOW.map(([d, arrow]) => (
            <path key={d} d={d} markerEnd={arrow ? "url(#arw)" : undefined} />
          ))}
        </g>

        <g className="comet">
          {PULSE.map((d, i) =>
            COMET.map((c, j) => (
              <path
                key={`${i}-${j}`}
                d={d}
                pathLength={100}
                strokeWidth={c.w}
                strokeDasharray={`${c.dash} ${100 - c.dash}`}
                opacity={c.o}
                style={
                  {
                    "--lag": `${c.lag}px`,
                    animationDelay: `-${((i * 0.44) % 3.6).toFixed(2)}s`,
                  } as CSSProperties
                }
              />
            )),
          )}
        </g>

        {/* ring ports — where a run taps a card rather than arriving at it */}
        {PORTS.map(([cx, cy], i) => (
          <g key={`${cx}-${cy}`}>
            <circle cx={cx} cy={cy} r="5" fill="#04150d" stroke="#3ddc8c" strokeWidth="1.4" />
            <circle cx={cx} cy={cy} r="2" fill="#6ce9a6" />
            <circle
              className="node-dot"
              cx={cx}
              cy={cy}
              r="5"
              fill="none"
              stroke="#6ce9a6"
              strokeWidth="1"
              style={{ animationDelay: `${i * 0.5}s` }}
            />
          </g>
        ))}
      </svg>

      </Reveal>
      <Reveal delay={0.12}>
      {/* what the exchange is actually feeding down the pipe: every symbol on the
          board, streaming into the decision. The strip is rendered TWICE and
          translated by exactly one list height, so the loop has no seam — the
          same trick <Wave> uses. */}
      {/* the symbols streaming into the exchange. The strip carries the list
          TWICE and translates by exactly -50%, which is one copy however the
          browser rounds the box widths — so the wrap has no seam. */}
      <div
        className="newsfeed pointer-events-none absolute overflow-hidden"
        style={S({
          left: rt(BOX.tech) + GAP,
          top: FEED - 20,
          width: BOX.hub.x - (rt(BOX.tech) + GAP),
          height: 17,
          fontSize: 8.5,
          letterSpacing: 0.2,
        })}
      >
        <div
          style={
            {
              "--dur": `${Math.round((TICKER_SYMBOLS.length * TICKER_BOX) / TICKER_SPEED)}s`,
            } as CSSProperties
          }
        >
          {[0, 1].map((copy) =>
            TICKER_SYMBOLS.map((sym) => <span key={`${copy}-${sym}`}>{sym}</span>),
          )}
        </div>
      </div>

      {LANES.map((lane, i) => (
        <div
          key={i}
          className="packet pointer-events-none absolute text-center"
          style={
            {
              ...S({ left: CENTRE - 38, top: BOX.hub.y + BOX.hub.h + GAP + 4, width: 76, fontSize: 8.5, letterSpacing: 0.2 }),
              color: "#a6f4c5",
              "--travel": `${BOX.exec.y - GAP - (BOX.hub.y + BOX.hub.h + GAP) - 18}rem`,
              "--pass": `${PASS}s`,
              "--delay": `-${(i * (PASS / LANE_COUNT)).toFixed(3)}s`,
            } as CSSProperties
          }
        >
          <span>
            <Ticker values={lane} dur={lane.length * PASS} delay={-i * (PASS / LANE_COUNT)} />
          </span>
        </div>
      ))}

      {/* the exchange — the junction the page flows through, and the 13 indices
          it is the sum of */}
      <Card
        x={BOX.hub.x}
        y={BOX.hub.y}
        w={BOX.hub.w}
        h={BOX.hub.h}
        className="sheer drift"
        style={{ animationDelay: "-3s", "--amp": "3.5rem" } as CSSProperties}
      >
        <div
          className="absolute"
          style={{
            ...S({ left: 18, right: 18, top: 51, height: 1 }),
            background:
              "linear-gradient(90deg,transparent,rgba(108,233,166,0.28),transparent)",
          }}
        />

        {/* one sub-index at a time — all 13 share one keyframe and a negative
            delay puts each in its own slot, so exactly one is face-on */}
        <div
          className="flip absolute text-center"
          style={S({ left: 8, right: 8, top: 56, height: 48 })}
        >
          {SECTORS.map((name, i) => (
            <span
              key={name}
              style={{
                ...S({ fontSize: 16.5, lineHeight: "21rem", letterSpacing: 0.1 }),
                color: "#eaf6f0",
                animationDelay: `-${(i * FLIP_S).toFixed(2)}s`,
                animationDuration: `${(SECTORS.length * FLIP_S).toFixed(2)}s`,
              }}
            >
              {name}
            </span>
          ))}
        </div>

      </Card>

      {/* The seal sits ON the box's top edge, half in and half out — so it has to
          live OUTSIDE the Card, which is overflow-hidden and would clip it. It
          carries the same drift and delay as the box, or the two come apart as
          they move. hue-rotate swings the seal's blue to green, and multiply lets
          the light-green disc replace its white, so it reads as a green coin
          rather than a pasted-on white one. */}
      <div
        className="drift shine absolute flex items-center justify-center overflow-hidden"
        style={{
          ...S({
            left: 640 - 43,
            top: BOX.hub.y - 43,
            width: 86,
            height: 86,
            borderRadius: 99,
            borderWidth: 1,
          }),
          background: "radial-gradient(circle at 50% 34%,#dff8ec 0%,#b9ebd3 62%,#8ed7b7 100%)",
          borderStyle: "solid",
          borderColor: "rgba(134,239,172,0.75)",
          boxShadow: "0 0 36rem rgba(50,213,131,0.55), 0 10rem 22rem rgba(0,0,0,0.45)",
          animationDelay: "-3s",
          "--amp": "3.5rem",
        } as CSSProperties}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/nepse.png"
          alt="Nepal Stock Exchange"
          style={{
            ...S({ width: 78, height: 78 }),
            filter: "hue-rotate(-74deg) saturate(1.55) brightness(0.96)",
            mixBlendMode: "multiply",
          }}
        />
      </div>

      </Reveal>
      <Reveal delay={0.22}>
      {/* Technical Analyst — big reading over a scrolling area chart */}
      <Card
        x={BOX.tech.x}
        y={BOX.tech.y}
        w={BOX.tech.w}
        h={BOX.tech.h}
        className="frost drift"
        style={{ animationDelay: "-0.4s", "--amp": "3rem" } as CSSProperties}
      >
        <Head icon={TrendingUp} title="Technical Analyst" color="#eaf6f0" top={15} />
        <Rule top={40} />
        <Row k="NEPSE" v="RSI 14" top={48} vColor="#ecf5f1" />
        <div
          className="absolute flex items-baseline justify-between"
          style={S({ left: 18, right: 18, top: 66 })}
        >
          <span style={S({ fontSize: 27, lineHeight: "1", color: "#ffffff", fontWeight: 400 })}>
            <Ticker values={["62.45", "63.10", "62.67", "63.42", "62.88", "63.21"]} dur={4.8} />
          </span>
          <span style={S({ fontSize: 14, color: "#6ce9a6" })}>
            <Swap
              buy={
                <Ticker
                  values={["+2.35%", "+2.61%", "+2.12%", "+2.48%", "+2.29%", "+2.55%"]}
                  dur={6.6}
                />
              }
              sell={
                <Ticker
                  values={["-1.94%", "-2.28%", "-1.71%", "-2.463%", "-2.05%", "-1.88%"]}
                  dur={6.6}
                />
              }
            />
          </span>
        </div>
        <Wave
          w={304}
          h={44}
          id="tech"
          from="#6ce9a6"
          stroke="#a6f4c5"
          dur={14}
          d="M0,28 C22,24 36,13 58,15 S90,29 116,26 S157,13 183,11 S223,22 243,24 S290,31 304,28"
        />
      </Card>

      {/* Fundamental Analyst — key/value ledger */}
      <Card
        x={BOX.fund.x}
        y={BOX.fund.y}
        w={BOX.fund.w}
        h={BOX.fund.h}
        className="drift"
        style={{ animationDelay: "-2.2s", "--amp": "2.4rem" } as CSSProperties}
      >
        <Head icon={Landmark} title="Fundamental Analyst" />
        <Rule top={42} />
        <Row k="Status" v="Scoring" top={44} dot="#f0b429" />
        <Row
          k="P/E"
          v={<Ticker values={["18.4x", "18.7x", "18.2x", "18.9x", "18.5x", "18.3x"]} dur={5.4} />}
          top={66}
        />
        <Row
          k="Book Value"
          v={<Ticker values={["2.14x", "2.16x", "2.11x", "2.18x", "2.13x", "2.17x"]} dur={6.6} />}
          top={88}
        />
        <Row
          k="Div Yield"
          v={<Ticker values={["3.42%", "3.39%", "3.45%", "3.41%", "3.44%", "3.40%"]} dur={7.8} />}
          top={110}
        />
      </Card>

      {/* News Aggregation — score over a filling meter */}
      <Card
        x={BOX.news.x}
        y={BOX.news.y}
        w={BOX.news.w}
        h={BOX.news.h}
        className="drift"
        style={{ animationDelay: "-4.6s", "--amp": "2.8rem" } as CSSProperties}
      >
        <Head icon={Newspaper} title="News Aggregation" />
        <Rule top={42} />
        <div
          className="absolute flex items-baseline justify-between"
          style={S({ left: 18, right: 18, top: 44 })}
        >
          <span style={S({ fontSize: 24, lineHeight: "1", color: "#ffffff", fontWeight: 400 })}>
            <Ticker values={["68", "71", "66", "69", "72", "67"]} dur={5.4} />
            <span style={S({ fontSize: 11, color: LABEL })}>/100</span>
          </span>
          <span className="flex items-baseline" style={{ ...S({ gap: 6, fontSize: 11 }), color: LABEL }}>
            Headlines
            <b style={{ ...S({ fontSize: 12.5 }), color: VALUE, fontWeight: 400 }}>
              <Ticker values={["128", "131", "134", "136", "139", "142"]} dur={4.2} />
            </b>
          </span>
        </div>
        <div
          className="absolute overflow-hidden"
          style={{
            ...S({ left: 18, right: 18, top: 76, height: 7, borderRadius: 99 }),
            background: "rgba(255,255,255,0.10)",
          }}
        >
          <i
            className="meter block h-full w-full"
            style={{ background: "linear-gradient(90deg,#039855,#6ce9a6)" }}
          />
        </div>
        <Row k="" v={<Swap buy="Bullish  ·  Last 24h" sell="Bearish  ·  Last 24h" />} top={92} size={10.5} />
      </Card>

      {/* Net churn — the transparent square, and it floats */}
      <Card
        x={BOX.churn.x}
        y={BOX.churn.y}
        w={BOX.churn.w}
        h={BOX.churn.h}
        className="sheer drift"
        style={{ animationDelay: "-1.5s", "--amp": "4rem" } as CSSProperties}
      >
        <div
          className="absolute flex flex-col items-center justify-center text-center"
          style={S({ inset: 0, gap: 6 })}
        >
          <span style={{ ...S({ fontSize: 10, letterSpacing: 0.4 }), color: LABEL }}>
            Net churn
          </span>
          <span style={S({ fontSize: 30, lineHeight: "1", color: "#a6f4c5", fontWeight: 400 })}>
            <Swap
              buy={<Ticker values={["+24%", "+19%", "+27%", "+22%", "+26%", "+21%"]} dur={4.2} />}
              sell={<Ticker values={["-18%", "-23%", "-16%", "-21%", "-19%", "-25%"]} dur={4.2} />}
            />
          </span>
          <span style={{ ...S({ fontSize: 9.5, letterSpacing: 0.4 }), color: LABEL }}>5D</span>
        </div>
      </Card>

      {/* Brokers — the square, per-broker columns breathing */}
      <Card
        x={BOX.brok.x}
        y={BOX.brok.y}
        w={BOX.brok.w}
        h={BOX.brok.h}
        className="drift"
        style={{ animationDelay: "-6.1s", "--amp": "2.6rem" } as CSSProperties}
      >
        <Head icon={Users} title="Brokers" />
        <Rule top={42} />
        <div
          className="bars absolute flex items-end"
          style={S({ left: 18, right: 18, bottom: 16, height: 76, gap: 3 })}
        >
          {BARS.slice(0, 10).map(([a, b], i) => (
            <i
              key={i}
              className="h-full flex-1"
              style={
                {
                  ...S({ borderRadius: 3 }),
                  background: "linear-gradient(180deg,#6ce9a6,rgba(3,152,85,0.35))",
                  "--a": a,
                  "--b": b,
                  animationDelay: `${(i % 5) * 0.22}s`,
                } as CSSProperties
              }
            />
          ))}
        </div>
      </Card>

      </Reveal>
      <Reveal delay={0.3}>
      {/* Order Execution — what the exchange's read turned into */}
      <Card
        x={BOX.exec.x}
        y={BOX.exec.y}
        w={BOX.exec.w}
        h={BOX.exec.h}
        className="frost drift"
        style={{ animationDelay: "-5.3s", "--amp": "2.2rem" } as CSSProperties}
      >
        <Head icon={ClipboardList} title="Order Execution" color="#f2fbf6" top={22} />
        <Rule top={47} />
        <Row
          k="Status"
          v={<Swap buy="Bullish" sell="Bearish" />}
          top={64}
          dot="#32d583"
        />
        <span
          className="on-buy absolute"
          style={S({ left: 18, top: 88, fontSize: 46, lineHeight: "1", color: "#a6f4c5", fontWeight: 400 })}
        >
          Buy
        </span>
        <span
          className="on-sell absolute"
          style={S({ left: 18, top: 88, fontSize: 46, lineHeight: "1", color: "#a6f4c5", fontWeight: 400 })}
        >
          Sell
        </span>
        <Row
          k="Confidence"
          v={<Ticker values={["76%", "79%", "74%", "78%", "80%", "75%"]} dur={5.4} />}
          top={158}
        />
        <div
          className="absolute overflow-hidden"
          style={{
            ...S({ left: 18, right: 18, top: 180, height: 8, borderRadius: 99 }),
            background: "rgba(255,255,255,0.12)",
          }}
        >
          <i
            className="meter block h-full w-full"
            style={{ background: "linear-gradient(90deg,#039855,#6ce9a6)" }}
          />
        </div>
        <Row
          k="Expected Return"
          v={
            <Swap
              buy={<Ticker values={["+8.95%", "+9.12%", "+8.74%", "+8.88%", "+9.24%"]} dur={5.4} />}
              sell={<Ticker values={["-7.42%", "-6.88%", "-7.95%", "-7.13%", "-8.06%"]} dur={5.4} />}
            />
          }
          top={212}
        />
        <Row
          k="Risk Score"
          v={<Ticker values={["23/100", "24/100", "22/100", "25/100"]} dur={7.6} />}
          top={246}
        />
        <Row
          k="Min Probability"
          v={<Ticker values={["66.4%", "67.1%", "65.8%", "66.9%", "66.2%", "67.5%"]} dur={6.6} />}
          top={280}
        />
      </Card>

      </Reveal>
      <Reveal delay={0.34}>
      {/* Risk Filter */}
      <Card
        x={BOX.risk.x}
        y={BOX.risk.y}
        w={BOX.risk.w}
        h={BOX.risk.h}
        className="drift"
        style={{ animationDelay: "-7.4s", "--amp": "2.9rem" } as CSSProperties}
      >
        <Head icon={ShieldCheck} title="Risk Filter" />
        <Rule top={42} />
        <Row k="Status" v={<Swap buy="Passed" sell="Elevated" />} top={56} dot="#32d583" />
        <Row
          k="Risk Score"
          v={<Ticker values={["23/100", "25/100", "22/100", "21/100", "26/100"]} dur={6} />}
          top={88}
        />
        <Row
          k="Position Size"
          v={<Ticker values={["250 kitta", "245 kitta", "260 kitta", "252 kitta"]} dur={9.2} />}
          top={120}
        />
        <Row
          k="Risk"
          v={<Ticker values={["1.32%", "1.28%", "1.35%", "1.30%", "1.33%", "1.27%"]} dur={7.2} />}
          top={152}
        />
      </Card>

      {/* Sentiment Analysis */}
      <Card
        x={BOX.sent.x}
        y={BOX.sent.y}
        w={BOX.sent.w}
        h={BOX.sent.h}
        className="drift"
        style={{ animationDelay: "-8.2s", "--amp": "3.2rem" } as CSSProperties}
      >
        <Head icon={ChartColumn} title="Sentiment Analysis" />
        <Rule top={42} />
        <Row k="Last Update" v="Just now" top={56} />
        <Row
          k="Social Volume"
          v={
            <Swap
              buy={<Ticker values={["+24%", "+27%", "+21%", "+23%", "+28%"]} dur={5.4} />}
              sell={<Ticker values={["-17%", "-22%", "-15%", "-20%", "-24%"]} dur={5.4} />}
            />
          }
          top={84}
        />
        <Wave
          w={304}
          h={76}
          id="sent"
          from="#6ce9a6"
          stroke="#a6f4c5"
          dur={17}
          d="M0,54 L26,40 L48,56 L72,32 L96,47 L120,23 L146,43 L170,27 L196,49 L222,29 L248,45 L274,26 L304,54"
        />
      </Card>

      {/* Sentiment score — pulled out of the card above so the right column ends
          on a small floating tile instead of a third wide rectangle */}
      <Card
        x={BOX.score.x}
        y={BOX.score.y}
        w={BOX.score.w}
        h={BOX.score.h}
        className="sheer drift"
        style={{ animationDelay: "-3.8s", "--amp": "4.2rem" } as CSSProperties}
      >
        <div
          className="absolute flex flex-col items-center justify-center text-center"
          style={S({ inset: 0, gap: 6 })}
        >
          <span style={{ ...S({ fontSize: 10, letterSpacing: 0.4 }), color: LABEL }}>
            Sentiment
          </span>
          <span style={S({ fontSize: 26, lineHeight: "1", color: "#a6f4c5", fontWeight: 400 })}>
            <Ticker values={["68/100", "70/100", "66/100", "69/100", "67/100", "71/100"]} dur={6.6} />
          </span>
          <span style={{ ...S({ fontSize: 9.5, letterSpacing: 0.4 }), color: LABEL }}>24h</span>
        </div>
      </Card>

      </Reveal>
    </main>
  );
}
