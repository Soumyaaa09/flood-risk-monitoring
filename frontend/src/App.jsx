import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import DisclaimerBanner from './components/DisclaimerBanner';
import Navbar from './components/Navbar';
import HomePage from './pages/HomePage';
import GeospatialMapPage from './pages/GeospatialMapPage';
import RiskAssessmentPage from './pages/RiskAssessmentPage';
import HistoricalAnalyticsPage from './pages/HistoricalAnalyticsPage';
import MonitoringDashboardPage from './pages/MonitoringDashboardPage';
import AboutPage from './pages/AboutPage';
import AlertsPage from './pages/AlertsPage';
import WeatherPreviewPage from './pages/WeatherPreviewPage';

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen flex flex-col bg-slate-50 text-slate-900">
        <DisclaimerBanner />
        <Navbar />
        <main className="flex-1">
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/map" element={<GeospatialMapPage />} />
            <Route path="/risk" element={<RiskAssessmentPage />} />
            <Route path="/alerts" element={<AlertsPage />} />
            <Route path="/history" element={<HistoricalAnalyticsPage />} />
            <Route path="/monitoring" element={<MonitoringDashboardPage />} />
            <Route path="/about" element={<AboutPage />} />
            <Route path="/weather-preview" element={<WeatherPreviewPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
        <footer className="bg-white border-t border-slate-200 py-6 text-center text-xs text-slate-500">
          <div className="max-w-7xl mx-auto px-4 space-y-1">
            <p>
              FloodWatch — Odisha Rainfall Monitoring &amp; Decision-Support Platform (v0.1.0) &bull; Pilot Locations: Paradeep, Cuttack, Kendrapara (Mahanadi Delta, Odisha)
            </p>
            <p className="text-slate-400">
              Research & Decision-Support System &bull; Not an official emergency warning service. Consult IMD &amp; OSDMA for official evacuation orders.
            </p>
          </div>
        </footer>
      </div>
    </BrowserRouter>
  );
}
