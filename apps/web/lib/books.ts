export type AgeGroup = "0-3" | "3-5" | "6-8" | "9-12";
export type AccessLevel = "free" | "paid";
export type BookFormat = "epub" | "pdf" | "both";
export type CoverTheme = "sky" | "berry" | "meadow" | "sunshine";

export type MediaAsset = {
  url: string;
  alt?: string;
  width?: number;
  height?: number;
};

export type BookView = {
  id: string;
  title: string;
  slug: string;
  author: string;
  summary: string;
  ageGroup: AgeGroup;
  readMinutes: number;
  accessLevel: AccessLevel;
  format: BookFormat;
  coverTheme: CoverTheme;
  youtubeUrl?: string | null;
  cover?: MediaAsset | null;
  sampleImage?: MediaAsset | null;
  storyPreviewPages?: { page: number; text: string }[];
};

export const AGE_FILTERS = [
  "All ages",
  "0–3",
  "3–5",
  "6–8",
  "9–12",
] as const;

export type AgeFilter = (typeof AGE_FILTERS)[number];

export function formatAgeLabel(ageGroup: AgeGroup): string {
  return `Ages ${ageGroup.replace("-", "–")}`;
}

export function formatEditionLabel(format: BookFormat): string {
  if (format === "both") return "PDF + EPUB";
  return format.toUpperCase();
}

const CATALOG_BOOKS: BookView[] = [
  {
    id: "book-1",
    title: "Room on the Quilt",
    slug: "room-on-the-quilt",
    author: "Clara Meadowlark",
    summary:
      "Under a sun-dappled pear tree, Maya spreads out Grandmother's patchwork quilt for one quiet afternoon—until a curious beetle, a sleepy hedgehog, and three giggling cousins ask if there is room for one more.",
    ageGroup: "3-5",
    readMinutes: 6,
    accessLevel: "free",
    format: "both",
    coverTheme: "sunshine",
    youtubeUrl: "https://www.youtube.com/@giggleandsnooze",
    sampleImage: {
      url: "/assets/story-page.png",
      alt: "A child sitting on a quilt under a pear tree",
    },
    storyPreviewPages: [
      {
        page: 1,
        text: "The pears above Whispering Hill smelled like warm honey, and Maya's quilt had twenty-four bright squares—plenty of room for one daydreamer and a basket of snacks.",
      },
      {
        page: 2,
        text: '"Excuse me," whispered a round little hedgehog with clover in his quills. "That yellow square looks just my size. Might there be room on the quilt?"',
      },
      {
        page: 3,
        text: "Maya scooted past two purple patches and patted the sunny corner. There was always a little more room when the afternoon breeze blew warm.",
      },
    ],
  },
  {
    id: "book-2",
    title: "The Sleepy Little Cloud",
    slug: "the-sleepy-little-cloud",
    author: "Nora Starling",
    summary:
      "A small cloud drifts across the evening sky looking for the softest hilltop, tallest pine, or quietest rooftop to curl up and dream until morning.",
    ageGroup: "0-3",
    readMinutes: 4,
    accessLevel: "free",
    format: "both",
    coverTheme: "sky",
    youtubeUrl: "https://www.youtube.com/@giggleandsnooze",
    storyPreviewPages: [
      {
        page: 1,
        text: "High above the sailboats and chimney tops, Pippin the cloud let out a tiny, misty yawn that turned pink in the sunset.",
      },
      {
        page: 2,
        text: "The mountain peak was too pointy, and the lighthouse blinked too bright—where does a sleepy cloud tuck in for the night?",
      },
    ],
  },
  {
    id: "book-3",
    title: "The Snail Mail Express",
    slug: "the-snail-mail-express",
    author: "Oliver Finch",
    summary:
      "Clara and Barnaby deliver the strangest parcels in Whisperwood Hollow—from bottled firefly glow to dandelion umbrellas—one unhurried mile at a time.",
    ageGroup: "6-8",
    readMinutes: 8,
    accessLevel: "paid",
    format: "both",
    coverTheme: "meadow",
    youtubeUrl: "https://www.youtube.com/@giggleandsnooze",
  },
  {
    id: "book-4",
    title: "Moonlight Over Blackberry Lane",
    slug: "moonlight-over-blackberry-lane",
    author: "Evelyn Brooks",
    summary:
      "When fireflies forget their bedtime lantern song, two badger siblings tiptoe down Blackberry Lane humming gentle rhymes to guide the forest to sleep.",
    ageGroup: "3-5",
    readMinutes: 5,
    accessLevel: "paid",
    format: "epub",
    coverTheme: "berry",
  },
  {
    id: "book-5",
    title: "Puddle Boots & Dandelion Wishes",
    slug: "puddle-boots-and-dandelion-wishes",
    author: "Milo Pippin",
    summary:
      "Splash, stomp, hooray! After a spring shower clears, little Leo counts rainbow puddles and sends fuzzy dandelion wishes floating over the garden gate.",
    ageGroup: "0-3",
    readMinutes: 4,
    accessLevel: "free",
    format: "pdf",
    coverTheme: "meadow",
    storyPreviewPages: [
      {
        page: 1,
        text: "Drip-drop stopped! Out came two yellow boots, ready for the biggest mirror-bright puddle in the garden path.",
      },
    ],
  },
  {
    id: "book-6",
    title: "The Lantern at Whisperwood Nook",
    slug: "the-lantern-at-whisperwood-nook",
    author: "Clara Meadowlark",
    summary:
      "Every Friday at dusk, Owl hangs an amber lantern outside the hollow oak library so woodland readers can swap stories and cocoa before lights-out.",
    ageGroup: "6-8",
    readMinutes: 7,
    accessLevel: "free",
    format: "both",
    coverTheme: "sunshine",
    youtubeUrl: "https://www.youtube.com/@giggleandsnooze",
    storyPreviewPages: [
      {
        page: 1,
        text: "Three taps on the oak root meant the Whisperwood reading nook was open, with cinnamon mugs steaming on the mossy shelf.",
      },
    ],
  },
  {
    id: "book-7",
    title: "Professor Barnaby's Starry Map",
    slug: "professor-barnabys-starry-map",
    author: "Oliver Finch",
    summary:
      "Armed with a brass telescope and a compass that points toward wonder, young astronomers chart constellations shaped like teapots, whales, and paper boats.",
    ageGroup: "9-12",
    readMinutes: 11,
    accessLevel: "paid",
    format: "both",
    coverTheme: "sky",
  },
  {
    id: "book-8",
    title: "The Clockwork Teacup",
    slug: "the-clockwork-teacup",
    author: "Beatrice Wren",
    summary:
      "In Auntie Thistle's curiosity shop, a porcelain teacup winds itself up at midnight to tell tall tales from every harbor it has ever sailed past.",
    ageGroup: "9-12",
    readMinutes: 10,
    accessLevel: "paid",
    format: "epub",
    coverTheme: "berry",
  },
];

export async function getPublishedBooks(): Promise<BookView[]> {
  return CATALOG_BOOKS;
}

export async function getBookBySlug(slug: string): Promise<BookView | null> {
  return CATALOG_BOOKS.find((book) => book.slug === slug) ?? null;
}
