import React from 'react';
import { Link } from 'react-router-dom';
import {
  Compass,
  Database,
  ExternalLink,
  Info,
  Layers,
  MapPin,
  ShieldAlert,
  ShieldCheck,
  Waves,
  Activity,
  AlertTriangle,
  CloudRain,
  Droplets,
  BookOpen,
} from 'lucide-react';

export default function AboutPage() {
  const officialAgencies = [
    {
      name: 'India Meteorological Department (IMD) - Bhubaneswar',
      role: 'Statutory weather warnings, cyclone alerts, and precipitation forecasts for Odisha.',
      url: 'https://mausam.imd.gov.in/bhubaneswar/',
      tag: 'Weather Authority',
    },
    {
      name: 'Odisha State Disaster Management Authority (OSDMA)',
      role: 'State disaster preparedness, evacuation directives, and emergency flood management.',
      url: 'https://www.osdma.org/',
      tag: 'Emergency Management',
    },
    {
      name: 'Central Water Commission (CWC) - Mahanadi Basin',
      role: 'National river water level, flood stage monitoring, and dam reservoir discharge tracking.',
      url: 'https://cwc.gov.in/',
      tag: 'Riverine Hydrology',
    },
  ];

  const pilotPoints = [
    {
      name: 'Paradeep Weather Grid Point',
      district: 'Jagatsinghpur',
      coords: '20.3160°N, 86.6110°E',
      type: 'Coastal Terminus Grid',
      desc: 'Monitors atmospheric precipitation at the mouth of the Mahanadi estuary where intense cyclonic convective rainfall meets coastal storm surges.',
    },
    {
      name: 'Cuttack Weather Grid Point',
      district: 'Cuttack',
      coords: '20.4625°N, 85.8830°E',
      type: 'Delta Apex Grid',
      desc: 'Monitors precipitation at the head of the Mahanadi delta bifurcation into the Kathajodi and Mahanadi main stem channels.',
    },
    {
      name: 'Kendrapara Weather Grid Point',
      district: 'Kendrapara',
      coords: '20.5017°N, 86.4223°E',
      type: 'Interfluvial Floodplain Grid',
      desc: 'Tracks precipitation across the low-lying Brahmani-Mahanadi interfluvial coastal plain susceptible to drainage congestion.',
    },
  ];

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-10 space-y-12">
      {/* Header */}
      <div className="max-w-3xl space-y-4">
        <div className="inline-flex items-center gap-2 text-xs font-bold text-sky-700 uppercase tracking-wider bg-sky-50 px-3 py-1 rounded-full border border-sky-100">
          <BookOpen className="w-3.5 h-3.5" />
          About FloodWatch Platform
        </div>
        <h1 className="text-3xl sm:text-4xl font-extrabold text-slate-900 tracking-tight">
          Scientific Decision-Support for Odisha's Delta Catchments
        </h1>
        <p className="text-base sm:text-lg text-slate-600 leading-relaxed">
          FloodWatch is an open, transparent hydrological risk intelligence foundation designed
          to evaluate atmospheric rainfall, forward-looking numerical weather forecasts, and
          multi-window antecedent moisture indicators across Odisha's coastal river deltas.
        </p>
      </div>

      {/* Core Principles Grid */}
      <section className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-3">
          <div className="w-10 h-10 rounded-xl bg-sky-50 text-sky-700 flex items-center justify-center">
            <CloudRain className="w-5 h-5" />
          </div>
          <h2 className="text-lg font-bold text-slate-900">Atmospheric Nowcasting</h2>
          <p className="text-sm text-slate-600 leading-relaxed">
            Continuously ingests localized model nowcasts and forward 24-hour numerical forecasts
            from Open-Meteo, evaluated against research threshold bands calibrated for Odisha catchments.
          </p>
        </div>

        <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-3">
          <div className="w-10 h-10 rounded-xl bg-indigo-50 text-indigo-700 flex items-center justify-center">
            <Droplets className="w-5 h-5" />
          </div>
          <h2 className="text-lg font-bold text-slate-900">Antecedent Moisture (ARI)</h2>
          <p className="text-sm text-slate-600 leading-relaxed">
            Calculates an empirical Antecedent Rainfall Indicator (weighted accumulation:
            0.5 &times; 24h + 0.3 &times; 72h + 0.2 &times; 7d) to gauge catchment surface water readiness
            before new downpours arrive.
          </p>
        </div>

        <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-3">
          <div className="w-10 h-10 rounded-xl bg-emerald-50 text-emerald-700 flex items-center justify-center">
            <Layers className="w-5 h-5" />
          </div>
          <h2 className="text-lg font-bold text-slate-900">Strict Metric Isolation</h2>
          <p className="text-sm text-slate-600 leading-relaxed">
            Rainfall depth (mm), river water level (m), and discharge (m³/s) are segregated.
            Atmospheric rainfall is never conflated with confirmed river embankment breaches.
          </p>
        </div>
      </section>

      {/* Pilot Virtual Weather Points */}
      <section className="bg-white rounded-2xl border border-slate-200 p-8 shadow-sm space-y-6">
        <div>
          <div className="flex items-center gap-2 text-xs font-bold text-slate-500 uppercase tracking-wider">
            <MapPin className="w-4 h-4 text-sky-600" />
            Geographic Coverage
          </div>
          <h2 className="text-xl font-extrabold text-slate-900 mt-1">Pilot Grid Points in Odisha</h2>
          <p className="text-sm text-slate-600 mt-1">
            Locations represent model grid points at pilot coordinates in the Mahanadi river basin.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {pilotPoints.map((pt) => (
            <div key={pt.name} className="p-5 rounded-xl border border-slate-200 bg-slate-50/50 space-y-2.5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold text-sky-700 bg-sky-50 px-2 py-0.5 rounded border border-sky-100">
                  {pt.district}
                </span>
                <span className="text-[11px] font-mono text-slate-400">{pt.coords}</span>
              </div>
              <h3 className="font-bold text-slate-900 text-base">{pt.name}</h3>
              <p className="text-xs text-slate-600 leading-relaxed">{pt.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Official Emergency Authority & Public Warning Directory */}
      <section className="bg-white rounded-2xl border border-slate-200 p-8 shadow-sm space-y-6">
        <div>
          <div className="flex items-center gap-2 text-xs font-bold text-amber-700 uppercase tracking-wider">
            <ShieldCheck className="w-4 h-4 text-amber-600" />
            Statutory Public Warning Resources
          </div>
          <h2 className="text-xl font-extrabold text-slate-900 mt-1">Official Government Warning Authorities</h2>
          <p className="text-sm text-slate-600 mt-1">
            FloodWatch is an independent research platform. Always refer directly to statutory
            authorities for official emergency advisories, cyclone alerts, and evacuation notices.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {officialAgencies.map((agency) => (
            <div
              key={agency.name}
              className="p-6 rounded-xl border border-slate-200 bg-slate-50/40 flex flex-col justify-between space-y-4 hover:border-slate-300 transition"
            >
              <div className="space-y-2">
                <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500 bg-slate-200/60 px-2 py-0.5 rounded">
                  {agency.tag}
                </span>
                <h3 className="font-bold text-slate-900 text-sm">{agency.name}</h3>
                <p className="text-xs text-slate-600 leading-relaxed">{agency.role}</p>
              </div>
              <a
                href={agency.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 text-xs font-semibold text-sky-700 hover:text-sky-800 transition"
              >
                Visit Official Portal
                <ExternalLink className="w-3.5 h-3.5" />
              </a>
            </div>
          ))}
        </div>
      </section>

      {/* Research Disclaimers */}
      <section className="bg-amber-50/70 rounded-2xl border border-amber-200 p-8 space-y-4">
        <div className="flex items-center gap-2 text-amber-900 font-bold text-base">
          <ShieldAlert className="w-5 h-5 text-amber-700 shrink-0" />
          Mandatory Research & Decision-Support Notice
        </div>
        <div className="text-xs sm:text-sm text-amber-950 space-y-2.5 leading-relaxed">
          <p>
            <strong>Virtual Telemetry Notice:</strong> Pilot monitoring stations are virtual numerical model grid points
            derived from Open-Meteo API integrations. They do <strong>not</strong> represent physical ground rain-gauge
            stations or automatic weather stations (AWS) operated by IMD or the Government of Odisha.
          </p>
          <p>
            <strong>No Inundation Confirmation:</strong> Flood risk categories reflect atmospheric surface runoff potential
            based on precipitation accumulations and forward forecasts. They do <strong>not</strong> simulate or confirm
            physical river overtopping, embankment breaches, localized water depths, or affected population numbers.
          </p>
          <p>
            <strong>Data Reliability Protocol:</strong> Missing or stale telemetry is classified as <code>UNAVAILABLE</code> and
            is never falsely replaced with zero rainfall.
          </p>
        </div>
      </section>

      {/* Quick Launch Bar */}
      <div className="flex flex-wrap items-center justify-between gap-4 p-6 bg-slate-900 text-white rounded-2xl">
        <div>
          <h3 className="font-bold text-base">Ready to explore active rainfall telemetry?</h3>
          <p className="text-xs text-slate-400 mt-0.5">
            View live geospatial maps, 7-day historical trends, and risk assessment indicators.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Link
            to="/map"
            className="px-4 py-2 bg-sky-600 hover:bg-sky-500 text-white text-xs font-bold rounded-lg transition"
          >
            Launch Map View
          </Link>
          <Link
            to="/risk"
            className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-white text-xs font-bold rounded-lg transition"
          >
            View Risk Engine
          </Link>
        </div>
      </div>
    </div>
  );
}
