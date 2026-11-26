import React from 'react';
import { AlertTriangle } from 'lucide-react';

export default function DisclaimerBanner() {
  return (
    <div className="bg-amber-500/10 border-b border-amber-500/30 text-amber-900 px-4 py-2.5 text-xs sm:text-sm font-medium">
      <div className="max-w-7xl mx-auto flex items-center gap-2">
        <AlertTriangle className="w-4 h-4 text-amber-600 flex-shrink-0" />
        <span>
          <strong>RESEARCH PROTOTYPE:</strong> FloodWatch is a decision-support and risk-monitoring prototype,
          <span className="underline ml-1">NOT an official emergency-warning service</span>. Coordinates used are for API connectivity testing and do not represent official gauge stations.
        </span>
      </div>
    </div>
  );
}
