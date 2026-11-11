export interface Challenge {
  id: string;
  title: string;
  description: string;
  category: "deforestation" | "agriculture" | "urban" | "climate";
  status: "active" | "completed" | "upcoming";
  participants: number;
  submissions: number;
  deadline: string;
  prize: string;
  progress: number;
  leaderboard: LeaderboardEntry[];
}

export interface LeaderboardEntry {
  rank: number;
  username: string;
  score: number;
  submissions: number;
}

export interface ActivityFeedItem {
  id: string;
  type: "insight" | "dataset" | "comment" | "challenge_join" | "workflow_shared";
  username: string;
  avatar: string;
  text: string;
  timestamp: string;
}

export const challenges: Challenge[] = [
  {
    id: "ch1",
    title: "Map Deforestation in Amazon Basin Q1 2026",
    description: "Identify and map deforestation events exceeding 5 hectares using Sentinel-2 imagery across the Brazilian Amazon region. Submissions scored on accuracy and coverage.",
    category: "deforestation",
    status: "active",
    participants: 142,
    submissions: 89,
    deadline: "2026-03-31",
    prize: "$5,000 + Featured Profile",
    progress: 67,
    leaderboard: [
      { rank: 1, username: "geo_analyst_42", score: 94.2, submissions: 12 },
      { rank: 2, username: "sentinel_watcher", score: 91.8, submissions: 8 },
      { rank: 3, username: "forest_guardian", score: 89.5, submissions: 15 },
      { rank: 4, username: "earthlab_dev", score: 87.1, submissions: 6 },
      { rank: 5, username: "ndvi_expert", score: 85.9, submissions: 10 },
    ],
  },
  {
    id: "ch2",
    title: "Urban Heat Island Detection — US Metro Areas",
    description: "Use thermal imagery to map and classify urban heat islands across the top 20 US metropolitan areas. Bonus points for temporal analysis.",
    category: "urban",
    status: "active",
    participants: 78,
    submissions: 34,
    deadline: "2026-04-15",
    prize: "$3,000 + Dataset Access",
    progress: 43,
    leaderboard: [
      { rank: 1, username: "thermal_mapper", score: 92.0, submissions: 7 },
      { rank: 2, username: "city_planner_ai", score: 88.4, submissions: 5 },
      { rank: 3, username: "heat_detective", score: 86.7, submissions: 9 },
    ],
  },
  {
    id: "ch3",
    title: "Crop Health Benchmark — Midwest Corn Belt",
    description: "Create a comprehensive health benchmark for corn fields across Iowa, Illinois, and Indiana using multi-temporal NDVI analysis.",
    category: "agriculture",
    status: "active",
    participants: 203,
    submissions: 156,
    deadline: "2026-05-01",
    prize: "$7,500 + API Credits",
    progress: 82,
    leaderboard: [
      { rank: 1, username: "corn_belt_pro", score: 96.1, submissions: 18 },
      { rank: 2, username: "ag_intelligence", score: 93.4, submissions: 14 },
      { rank: 3, username: "field_analyst", score: 91.2, submissions: 11 },
    ],
  },
  {
    id: "ch4",
    title: "Glacier Retreat Monitoring — Himalayan Range",
    description: "Track and quantify glacier retreat across 50 key glaciers in the Himalayan range using Landsat and Sentinel data from 2020-2026.",
    category: "climate",
    status: "upcoming",
    participants: 0,
    submissions: 0,
    deadline: "2026-06-30",
    prize: "$10,000 + Publication Credit",
    progress: 0,
    leaderboard: [],
  },
];

export const activityFeed: ActivityFeedItem[] = [
  { id: "af1", type: "insight", username: "geo_analyst_42", avatar: "GA", text: "Published new deforestation detection model with 94.2% accuracy", timestamp: "2 min ago" },
  { id: "af2", type: "dataset", username: "sentinel_watcher", avatar: "SW", text: "Uploaded 1.2TB Sentinel-2 mosaic for Amazon basin", timestamp: "8 min ago" },
  { id: "af3", type: "challenge_join", username: "thermal_mapper", avatar: "TM", text: "Joined Urban Heat Island Detection challenge", timestamp: "15 min ago" },
  { id: "af4", type: "comment", username: "corn_belt_pro", avatar: "CB", text: "Shared tips on multi-temporal NDVI normalization techniques", timestamp: "23 min ago" },
  { id: "af5", type: "workflow_shared", username: "earthlab_dev", avatar: "EL", text: "Published 'Automated Crop Stress Pipeline' workflow template", timestamp: "31 min ago" },
  { id: "af6", type: "insight", username: "field_analyst", avatar: "FA", text: "Identified anomalous vegetation pattern in Northern Valley sector", timestamp: "45 min ago" },
  { id: "af7", type: "dataset", username: "ndvi_expert", avatar: "NE", text: "Contributed ground-truth validation data for 340 sample points", timestamp: "1 hr ago" },
  { id: "af8", type: "challenge_join", username: "ag_intelligence", avatar: "AI", text: "Submitted 14th entry to Crop Health Benchmark challenge", timestamp: "1.5 hr ago" },
];
