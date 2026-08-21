import React, { useState, useEffect } from 'react';
import { LayoutGrid, Palette, Type, Sparkles, Layers } from 'lucide-react';

export default function BoilerplateInspector() {
  const [activeTab, setActiveTab] = useState('layouts'); // 'layouts' | 'palettes' | 'typography'
  const [templates, setTemplates] = useState([]);
  const [palettes, setPalettes] = useState({});
  const [categories, setCategories] = useState({});
  const [filterPhotoCount, setFilterPhotoCount] = useState('all');
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function fetchData() {
      try {
        const [tplRes, palRes, catRes] = await Promise.all([
          fetch('/api/templates'),
          fetch('/api/palettes'),
          fetch('/api/categories')
        ]);

        if (tplRes.ok && palRes.ok) {
          const tplData = await tplRes.json();
          const palData = await palRes.json();
          const catData = catRes.ok ? await catRes.json() : {};
          setTemplates(tplData.templates || []);
          setPalettes(palData.palettes || {});
          setCategories(catData.categories || {});
        }
      } catch (err) {
        console.error("Inspector fetch error:", err);
      } finally {
        setIsLoading(false);
      }
    }
    fetchData();
  }, []);

  const filteredTemplates = templates.filter(t => {
    if (filterPhotoCount === 'all') return true;
    return t.photo_count === parseInt(filterPhotoCount);
  });

  if (isLoading) {
    return (
      <div className="step-card" style={{ textAlign: 'center', padding: '4rem' }}>
        <Sparkles size={36} color="#8B5CF6" style={{ marginBottom: '1rem' }} />
        <h3>Loading 20 Canonical Themes & Design System...</h3>
      </div>
    );
  }

  return (
    <div className="inspector-wrapper" style={{ width: '100%', maxWidth: '1000px', margin: '1rem auto' }}>
      {/* Sub-nav Tabs */}
      <div className="inspector-tabs" style={{ display: 'flex', gap: '1rem', marginBottom: '2rem', justifyContent: 'center' }}>
        <button
          className={`btn ${activeTab === 'layouts' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setActiveTab('layouts')}
        >
          <LayoutGrid size={18} />
          <span>Layout Boilerplates ({templates.length})</span>
        </button>

        <button
          className={`btn ${activeTab === 'palettes' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setActiveTab('palettes')}
        >
          <Palette size={18} />
          <span>20 Canonical Themes ({Object.keys(palettes).length})</span>
        </button>

        <button
          className={`btn ${activeTab === 'typography' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setActiveTab('typography')}
        >
          <Type size={18} />
          <span>Text & Category Matrix</span>
        </button>
      </div>

      {/* TAB 1: LAYOUT BOILERPLATES */}
      {activeTab === 'layouts' && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1.5rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: '#4B5563', fontWeight: 600 }}>
              <Layers size={20} color="#8B5CF6" />
              <span>Focal-Point Division Grids</span>
            </div>
            
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              {['all', '1', '2', '3', '4'].map(count => (
                <button
                  key={count}
                  className={`pill-tag ${filterPhotoCount === count ? 'selected' : ''}`}
                  onClick={() => setFilterPhotoCount(count)}
                >
                  {count === 'all' ? 'All Layouts' : `${count} Photo Grids`}
                </button>
              ))}
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(440px, 1fr))', gap: '2rem' }}>
            {filteredTemplates.map((tpl) => (
              <div key={tpl.id} className="step-card" style={{ padding: '1.5rem', marginBottom: 0 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                  <div>
                    <h4 style={{ fontSize: '1.05rem', fontWeight: 700, color: '#111827' }}>{tpl.name}</h4>
                    <span style={{ fontSize: '0.75rem', color: '#6B7280', fontFamily: 'monospace' }}>ID: {tpl.id}</span>
                  </div>
                  <span className="brand-badge">{tpl.photo_count} Photo(s) • {tpl.text_condition}</span>
                </div>

                <div className="spread-pair" style={{ height: '220px', borderRadius: '8px', border: '1px solid #E5E7EB', background: '#FAF9F6', position: 'relative' }}>
                  <div style={{
                    position: 'absolute', left: '50%', top: 0, bottom: 0, width: '2px',
                    background: 'rgba(239, 68, 68, 0.4)', zIndex: 30, pointerEvents: 'none'
                  }} title="Spine Gutter (x = 0.50)" />

                  {tpl.slots.map((slot, sIdx) => {
                    const isPhoto = slot.type === 'photo';
                    return (
                      <div
                        key={sIdx}
                        style={{
                          position: 'absolute',
                          left: `${slot.x_pct * 100}%`,
                          top: `${slot.y_pct * 100}%`,
                          width: `${slot.w_pct * 100}%`,
                          height: `${slot.h_pct * 100}%`,
                          background: isPhoto ? 'rgba(139, 92, 246, 0.15)' : 'rgba(236, 72, 153, 0.15)',
                          border: isPhoto ? '1.5px dashed #8B5CF6' : '1.5px dashed #EC4899',
                          borderRadius: '4px',
                          display: 'flex',
                          flexDirection: 'column',
                          alignItems: 'center',
                          justifyContent: 'center',
                          padding: '4px',
                          zIndex: 10
                        }}
                      >
                        <span style={{ fontSize: '0.75rem', fontWeight: 700, color: isPhoto ? '#6D28D9' : '#BE185D' }}>
                          {slot.role} ({slot.type})
                        </span>
                        {isPhoto && (
                          <span style={{ fontSize: '0.65rem', color: '#4C1D95', marginTop: '2px' }}>
                            AR: {slot.target_aspect || 1.0} | mode: {slot.render_mode || 'cover'}
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* TAB 2: 20 CANONICAL THEMES & 5 SEMANTIC ROLES */}
      {activeTab === 'palettes' && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '1.5rem' }}>
          {Object.entries(palettes).map(([themeName, roles]) => (
            <div key={themeName} className="step-card" style={{ padding: '1.5rem', marginBottom: 0 }}>
              <h3 style={{ fontSize: '1.15rem', fontWeight: 700, color: '#111827', marginBottom: '1rem' }}>{themeName}</h3>
              
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                {Object.entries(roles).map(([roleName, hexValue]) => (
                  <div
                    key={roleName}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '0.5rem 0.75rem',
                      borderRadius: '8px',
                      backgroundColor: hexValue,
                      color: (roleName === 'text' || roleName === 'accent') && hexValue !== '#FFFFFF' ? '#FFFFFF' : '#111827',
                      border: '1px solid rgba(0,0,0,0.08)'
                    }}
                  >
                    <span style={{ fontSize: '0.8rem', fontWeight: 600, textTransform: 'capitalize' }}>{roleName}</span>
                    <span style={{ fontSize: '0.8rem', fontFamily: 'monospace', fontWeight: 700 }}>{hexValue}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* TAB 3: TEXT & CATEGORY MATRIX */}
      {activeTab === 'typography' && (
        <div className="step-card" style={{ padding: '2.5rem' }}>
          <h3 style={{ fontSize: '1.4rem', fontWeight: 700, marginBottom: '1.5rem', color: '#111827' }}>
            Top 10 Categories & AI Theme Mapping Matrix
          </h3>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '1.5rem' }}>
            {Object.entries(categories).map(([catName, mappedThemes]) => (
              <div key={catName} style={{ background: '#F8FAFC', borderRadius: '12px', padding: '1.25rem', border: '1px solid #E2E8F0' }}>
                <h4 style={{ color: '#8B5CF6', marginBottom: '0.5rem' }}>{catName}</h4>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginTop: '0.5rem' }}>
                  {mappedThemes.map((t, idx) => (
                    <span key={idx} className="pill-tag selected" style={{ fontSize: '0.75rem', padding: '0.25rem 0.6rem' }}>
                      {t}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
