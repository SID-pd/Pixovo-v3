import React, { useState, useEffect } from 'react';
import { Activity, Clock, Database, HardDrive, RefreshCw, Cpu, CheckCircle2, AlertTriangle, Layers } from 'lucide-react';

export default function SystemStatsDashboard() {
  const [stats, setStats] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [lastRefreshed, setLastRefreshed] = useState(null);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const fetchStats = async () => {
    setIsLoading(true);
    try {
      const res = await fetch('/api/stats');
      if (res.ok) {
        const data = await res.json();
        setStats(data);
        setLastRefreshed(new Date());
      }
    } catch (err) {
      console.error("Failed to fetch system stats:", err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchStats();
    let interval = null;
    if (autoRefresh) {
      interval = setInterval(fetchStats, 3000);
    }
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [autoRefresh]);

  // Helper to format latency: >= 1000ms -> Seconds (e.g. 4.25 s), < 1000ms -> Milliseconds (e.g. 48.5 ms)
  const formatDuration = (ms) => {
    if (ms === null || ms === undefined || isNaN(ms)) return '0 ms';
    if (ms >= 1000) {
      return `${(ms / 1000).toFixed(2)} s`;
    }
    return `${Math.round(ms * 10) / 10} ms`;
  };

  return (
    <div style={{ maxWidth: '1200px', margin: '2rem auto', padding: '0 1.5rem', fontFamily: 'system-ui, sans-serif' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2rem' }}>
        <div>
          <h2 style={{ fontSize: '1.75rem', fontWeight: '700', color: '#111827', margin: 0, display: 'flex', alignItems: 'center', gap: '10px' }}>
            <Activity color="#8B5CF6" size={28} />
            Pixovo Real-Time Performance & System Stats
          </h2>
          <p style={{ color: '#6B7280', margin: '4px 0 0 0', fontSize: '0.95rem' }}>
            Live diagnostic telemetry, stage execution latency, and persistent SQLite storage metrics.
          </p>
        </div>

        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.85rem', color: '#4B5563', cursor: 'pointer' }}>
            <input 
              type="checkbox" 
              checked={autoRefresh} 
              onChange={(e) => setAutoRefresh(e.target.checked)} 
            />
            Auto-refresh (3s)
          </label>
          <button 
            onClick={fetchStats} 
            disabled={isLoading}
            className="btn btn-secondary"
            style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '6px 14px', borderRadius: '8px', border: '1px solid #D1D5DB', backgroundColor: '#FFFFFF', cursor: 'pointer' }}
          >
            <RefreshCw size={14} className={isLoading ? "animate-spin" : ""} />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {/* Overview Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1.25rem', marginBottom: '2rem' }}>
        <div style={{ backgroundColor: '#FFFFFF', padding: '1.25rem', borderRadius: '12px', boxShadow: '0 1px 3px rgba(0,0,0,0.1)', border: '1px solid #E5E7EB' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#6B7280', fontSize: '0.85rem', fontWeight: '500', marginBottom: '6px' }}>
            <Database size={18} color="#8B5CF6" />
            <span>Persisted Photos (SQLite)</span>
          </div>
          <div style={{ fontSize: '2rem', fontWeight: '700', color: '#111827' }}>
            {stats?.system?.persisted_photos ?? 0}
          </div>
          <div style={{ fontSize: '0.8rem', color: '#10B981', marginTop: '4px' }}>
            ● Survives server restarts
          </div>
        </div>

        <div style={{ backgroundColor: '#FFFFFF', padding: '1.25rem', borderRadius: '12px', boxShadow: '0 1px 3px rgba(0,0,0,0.1)', border: '1px solid #E5E7EB' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#6B7280', fontSize: '0.85rem', fontWeight: '500', marginBottom: '6px' }}>
            <Layers size={18} color="#EC4899" />
            <span>Photobook Jobs Stored</span>
          </div>
          <div style={{ fontSize: '2rem', fontWeight: '700', color: '#111827' }}>
            {stats?.system?.persisted_jobs ?? 0}
          </div>
          <div style={{ fontSize: '0.8rem', color: '#6B7280', marginTop: '4px' }}>
            Active memory cache: {stats?.system?.in_memory_active_jobs ?? 0}
          </div>
        </div>

        <div style={{ backgroundColor: '#FFFFFF', padding: '1.25rem', borderRadius: '12px', boxShadow: '0 1px 3px rgba(0,0,0,0.1)', border: '1px solid #E5E7EB' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#6B7280', fontSize: '0.85rem', fontWeight: '500', marginBottom: '6px' }}>
            <Cpu size={18} color="#3B82F6" />
            <span>Worker Concurrency</span>
          </div>
          <div style={{ fontSize: '2rem', fontWeight: '700', color: '#111827' }}>
            {stats?.system?.concurrency_limit ?? 4} <span style={{ fontSize: '1rem', fontWeight: 'normal', color: '#6B7280' }}>Slots</span>
          </div>
          <div style={{ fontSize: '0.8rem', color: '#3B82F6', marginTop: '4px' }}>
            Bounded async Semaphore
          </div>
        </div>

        <div style={{ backgroundColor: '#FFFFFF', padding: '1.25rem', borderRadius: '12px', boxShadow: '0 1px 3px rgba(0,0,0,0.1)', border: '1px solid #E5E7EB' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#6B7280', fontSize: '0.85rem', fontWeight: '500', marginBottom: '6px' }}>
            <HardDrive size={18} color="#F59E0B" />
            <span>Max File Size</span>
          </div>
          <div style={{ fontSize: '2rem', fontWeight: '700', color: '#111827' }}>
            {stats?.system?.max_file_size_mb ?? 20} <span style={{ fontSize: '1rem', fontWeight: 'normal', color: '#6B7280' }}>MB</span>
          </div>
          <div style={{ fontSize: '0.8rem', color: '#10B981', marginTop: '4px' }}>
            300 DPI Print Support
          </div>
        </div>
      </div>

      {/* Latency by Stage Table */}
      <div style={{ backgroundColor: '#FFFFFF', padding: '1.5rem', borderRadius: '12px', boxShadow: '0 1px 3px rgba(0,0,0,0.1)', border: '1px solid #E5E7EB', marginBottom: '2rem' }}>
        <h3 style={{ fontSize: '1.15rem', fontWeight: '600', color: '#111827', marginTop: 0, marginBottom: '1rem', display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Clock size={20} color="#8B5CF6" />
          Pipeline Execution Times (Average & Ranges)
        </h3>

        {stats?.performance?.stages && Object.keys(stats.performance.stages).length > 0 ? (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '0.9rem' }}>
              <thead>
                <tr style={{ borderBottom: '2px solid #E5E7EB', color: '#4B5563', backgroundColor: '#F9FAFB' }}>
                  <th style={{ padding: '10px 14px' }}>Pipeline Stage</th>
                  <th style={{ padding: '10px 14px' }}>Runs</th>
                  <th style={{ padding: '10px 14px' }}>Avg Latency</th>
                  <th style={{ padding: '10px 14px' }}>Min</th>
                  <th style={{ padding: '10px 14px' }}>Max</th>
                  <th style={{ padding: '10px 14px' }}>Last Run</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(stats.performance.stages).map(([stage, metric], idx) => (
                  <tr key={idx} style={{ borderBottom: '1px solid #E5E7EB', backgroundColor: idx % 2 === 0 ? '#FFFFFF' : '#F9FAFB' }}>
                    <td style={{ padding: '12px 14px', fontWeight: '600', color: '#1F2937' }}>{stage}</td>
                    <td style={{ padding: '12px 14px', color: '#4B5563' }}>{metric.count}</td>
                    <td style={{ padding: '12px 14px', fontWeight: '600', color: metric.avg_ms >= 1000 ? '#D97706' : '#059669' }}>
                      {formatDuration(metric.avg_ms)}
                    </td>
                    <td style={{ padding: '12px 14px', color: '#6B7280' }}>{formatDuration(metric.min_ms)}</td>
                    <td style={{ padding: '12px 14px', color: '#6B7280' }}>{formatDuration(metric.max_ms)}</td>
                    <td style={{ padding: '12px 14px', color: '#374151', fontWeight: '500' }}>{formatDuration(metric.last_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div style={{ padding: '2rem', textAlign: 'center', color: '#9CA3AF' }}>
            No stage operations recorded yet in this session. Run an upload or layout generation to view live benchmark metrics!
          </div>
        )}
      </div>

      {/* Recent Events Log */}
      <div style={{ backgroundColor: '#FFFFFF', padding: '1.5rem', borderRadius: '12px', boxShadow: '0 1px 3px rgba(0,0,0,0.1)', border: '1px solid #E5E7EB' }}>
        <h3 style={{ fontSize: '1.15rem', fontWeight: '600', color: '#111827', marginTop: 0, marginBottom: '1rem', display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Activity size={20} color="#10B981" />
          Recent Execution Events
        </h3>

        {stats?.performance?.recent_events && stats.performance.recent_events.length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {stats.performance.recent_events.slice(0, 15).map((evt, idx) => (
              <div 
                key={idx} 
                style={{ 
                  display: 'flex', 
                  justifyContent: 'space-between', 
                  alignItems: 'center',
                  padding: '10px 14px', 
                  backgroundColor: '#F9FAFB', 
                  borderRadius: '8px',
                  border: '1px solid #F3F4F6'
                }}
              >
                <div>
                  <span style={{ fontWeight: '600', color: '#1F2937', marginRight: '8px' }}>{evt.step}</span>
                  {evt.details && Object.keys(evt.details).length > 0 && (
                    <span style={{ fontSize: '0.8rem', color: '#6B7280' }}>
                      ({Object.entries(evt.details).map(([k, v]) => `${k}: ${v}`).join(', ')})
                    </span>
                  )}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                  <span style={{ fontSize: '0.85rem', fontWeight: '600', color: '#8B5CF6' }}>
                    {formatDuration(evt.elapsed_ms)}
                  </span>
                  <span style={{ fontSize: '0.75rem', color: '#9CA3AF' }}>
                    {new Date(evt.timestamp * 1000).toLocaleTimeString()}
                  </span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div style={{ padding: '1.5rem', textAlign: 'center', color: '#9CA3AF' }}>
            No recent events recorded.
          </div>
        )}
      </div>
    </div>
  );
}
