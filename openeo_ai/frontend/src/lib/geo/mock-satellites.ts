export interface Satellite {
  id: string;
  name: string;
  constellation: string;
  orbitType: "LEO" | "SSO" | "GEO";
  altitude: number;
  inclination: number;
  period: number;
  nextPass: string;
  coverage: number;
  status: "active" | "standby" | "maintenance";
  color: string;
}

export interface PassWindow {
  id: string;
  satelliteId: string;
  assetId: string;
  startTime: string;
  endTime: string;
  maxElevation: number;
  cloudCover: number;
}

export const satellites: Satellite[] = [
  {
    id: "s2a",
    name: "Sentinel-2A",
    constellation: "Copernicus",
    orbitType: "SSO",
    altitude: 786,
    inclination: 98.62,
    period: 100.6,
    nextPass: "14 min",
    coverage: 92,
    status: "active",
    color: "hsl(var(--status-good))",
  },
  {
    id: "s2b",
    name: "Sentinel-2B",
    constellation: "Copernicus",
    orbitType: "SSO",
    altitude: 786,
    inclination: 98.62,
    period: 100.6,
    nextPass: "2 hr 41 min",
    coverage: 88,
    status: "active",
    color: "hsl(var(--status-good))",
  },
  {
    id: "l9",
    name: "Landsat 9",
    constellation: "Landsat",
    orbitType: "SSO",
    altitude: 705,
    inclination: 98.2,
    period: 99.0,
    nextPass: "5 hr 12 min",
    coverage: 76,
    status: "active",
    color: "hsl(var(--status-warn))",
  },
  {
    id: "ps",
    name: "PlanetScope-41",
    constellation: "Planet",
    orbitType: "SSO",
    altitude: 475,
    inclination: 97.4,
    period: 94.0,
    nextPass: "38 min",
    coverage: 95,
    status: "active",
    color: "hsl(var(--status-good))",
  },
  {
    id: "wv3",
    name: "WorldView-3",
    constellation: "Maxar",
    orbitType: "SSO",
    altitude: 617,
    inclination: 97.2,
    period: 97.0,
    nextPass: "1 hr 58 min",
    coverage: 64,
    status: "standby",
    color: "hsl(var(--status-warn))",
  },
  {
    id: "s1a",
    name: "Sentinel-1A",
    constellation: "Copernicus",
    orbitType: "SSO",
    altitude: 693,
    inclination: 98.18,
    period: 98.6,
    nextPass: "3 hr 05 min",
    coverage: 81,
    status: "active",
    color: "hsl(var(--status-good))",
  },
];

export const passWindows: PassWindow[] = [
  { id: "pw1", satelliteId: "s2a", assetId: "northridge", startTime: "14:22", endTime: "14:28", maxElevation: 72, cloudCover: 12 },
  { id: "pw2", satelliteId: "ps", assetId: "northridge", startTime: "14:56", endTime: "15:02", maxElevation: 65, cloudCover: 8 },
  { id: "pw3", satelliteId: "s2b", assetId: "sunfield", startTime: "17:03", endTime: "17:09", maxElevation: 58, cloudCover: 22 },
  { id: "pw4", satelliteId: "l9", assetId: "greendale", startTime: "19:34", endTime: "19:40", maxElevation: 81, cloudCover: 5 },
  { id: "pw5", satelliteId: "wv3", assetId: "blackrock", startTime: "16:20", endTime: "16:25", maxElevation: 45, cloudCover: 35 },
  { id: "pw6", satelliteId: "s1a", assetId: "riverview", startTime: "17:27", endTime: "17:33", maxElevation: 68, cloudCover: 0 },
];
