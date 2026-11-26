import React from 'react';
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from 'recharts';

export default function RainfallChart({ data }) {
  if (!data || data.length === 0) {
    return (
      <div className="h-64 flex items-center justify-center text-slate-400 text-sm">
        No forecast data available to render chart.
      </div>
    );
  }

  // Format timestamp for display (e.g. "17 Nov 14:00")
  const chartData = data.slice(0, 48).map((point) => {
    const d = new Date(point.time);
    const label = `${d.getDate()} ${d.toLocaleString('default', { month: 'short' })} ${d
      .getHours()
      .toString()
      .padStart(2, '0')}:00`;
    return {
      time: label,
      rawTime: point.time,
      precipitation: point.precipitation_mm,
      probability: point.precipitation_probability_pct,
    };
  });

  return (
    <div className="w-full h-72">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 25 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
          <XAxis
            dataKey="time"
            angle={-35}
            textAnchor="end"
            interval={3}
            tick={{ fontSize: 11, fill: '#64748b' }}
            tickMargin={10}
          />
          <YAxis
            tick={{ fontSize: 11, fill: '#64748b' }}
            unit=" mm"
          />
          <Tooltip
            contentStyle={{
              backgroundColor: '#0f172a',
              borderRadius: '8px',
              border: 'none',
              color: '#fff',
              fontSize: '12px',
            }}
            formatter={(value) => [`${value} mm`, 'Precipitation']}
            labelFormatter={(label) => `Time: ${label}`}
          />
          <Bar dataKey="precipitation" fill="#0284c7" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
