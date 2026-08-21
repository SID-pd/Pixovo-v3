import React from 'react';
import { ChevronDown, Check, Shuffle } from 'lucide-react';

export default function BookCarousel3D({ variations, activeIdx, setActiveIdx, onScrollDown, onReshuffleVariations, isReshuffling }) {
  if (!variations || variations.length === 0) return null;

  return (
    <div style={{ width: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
      <h2 className="preview-title">Choose Your Album Variation (3 Saved Styles)</h2>

      {/* Reshuffle Variations Action Bar */}
      {onReshuffleVariations && (
        <button
          className="btn btn-secondary"
          onClick={onReshuffleVariations}
          disabled={isReshuffling}
          style={{ marginBottom: '1rem', padding: '0.5rem 1.25rem', borderRadius: '20px', fontWeight: 600 }}
        >
          <Shuffle size={16} color="#8B5CF6" />
          <span>{isReshuffling ? "Reshuffling Variations..." : "Reshuffle Palettes & Layouts (3 Variations)"}</span>
        </button>
      )}

      {/* 3D Book Carousel Stage */}
      <div className="carousel-stage">
        {variations.map((item, idx) => {
          const isHero = idx === activeIdx;

          return (
            <div
              key={item.id || idx}
              className={`carousel-card ${isHero ? 'hero' : ''}`}
              onClick={() => setActiveIdx(idx)}
              style={{
                borderColor: isHero ? item.accent_color : 'transparent'
              }}
            >
              {isHero && (
                <div className="hero-check-badge">
                  <Check size={18} />
                </div>
              )}

              {/* COVER STYLE 1: SPLIT BANNER */}
              {idx % 3 === 0 && (
                <div className="cover-layout-split" style={{ backgroundColor: item.base_color || '#FAF9F6' }}>
                  <img src={item.cover_image_url} alt="Cover top" className="cover-img-half" />
                  <div className="cover-banner-white">
                    <h4 style={{ color: item.text_color || '#1F2937' }}>{item.cover_title}</h4>
                  </div>
                  <img src={item.cover_image_url} alt="Cover bottom" className="cover-img-half" />
                </div>
              )}

              {/* COVER STYLE 2: SAGE GREEN / HERO BAND */}
              {idx % 3 === 1 && (
                <div className="cover-layout-sage" style={{ backgroundColor: item.base_color || '#8C9386' }}>
                  <div className="cover-img-top-wrapper">
                    <img src={item.cover_image_url} alt="Hero cover" className="cover-img-full" />
                  </div>
                  <div className="cover-sage-band" style={{ backgroundColor: item.accent_color || '#8C9386' }}>
                    <span className="cover-year-italic">{item.cover_subtitle || "2025"}</span>
                    <h4 className="serif-title" style={{ color: item.text_color || '#FFFFFF' }}>{item.cover_title}</h4>
                  </div>
                </div>
              )}

              {/* COVER STYLE 3: 4-PHOTO GLASS COLLAGE */}
              {idx % 3 === 2 && (
                <div className="cover-layout-collage">
                  <div className="collage-2x2">
                    <img src={item.cover_image_url} alt="c1" />
                    <img src={item.cover_image_url} alt="c2" />
                    <img src={item.cover_image_url} alt="c3" />
                    <img src={item.cover_image_url} alt="c4" />
                  </div>
                  <div className="cover-glass-overlay">
                    <h4 className="bold-serif-title" style={{ color: item.text_color || '#1F2937' }}>{item.cover_title}</h4>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <button className="scroll-indicator" onClick={onScrollDown} title="Scroll to double page spreads">
        <ChevronDown size={28} />
      </button>
    </div>
  );
}
