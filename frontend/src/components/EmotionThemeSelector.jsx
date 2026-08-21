import React from 'react';
import { Sparkles, Compass } from 'lucide-react';

const TOP_10_CATEGORIES = [
  "Family",
  "Travel",
  "Celebration",
  "Everyday",
  "Portraits",
  "Nature",
  "Lifestyle",
  "Milestones",
  "Activities",
  "Memories"
];

export default function EmotionThemeSelector({ userPrompt, setUserPrompt, onGenerate, isLoading, photoCount }) {
  const handlePillClick = (tag) => {
    setUserPrompt(tag);
  };

  return (
    <div className="step-card">
      <div className="step-header">
        <h2>Step 2: What is the Occasion or Emotion?</h2>
        <p>Tell Gemini AI about your photos to automatically match themes, colors, and layout captions</p>
      </div>

      <div style={{ position: 'relative', marginBottom: '1.5rem' }}>
        <input
          type="text"
          value={userPrompt}
          onChange={(e) => setUserPrompt(e.target.value)}
          placeholder="e.g. Friends visit to Kanpur Temple in 2025"
          style={{
            width: '100%',
            padding: '1rem 1.25rem',
            borderRadius: '12px',
            border: '1px solid #D1D5DB',
            fontSize: '1rem',
            outline: 'none',
            fontFamily: 'inherit'
          }}
        />
      </div>

      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem', fontSize: '0.85rem', color: '#6B7280' }}>
          <Compass size={16} />
          <span>Top 10 Combined Categories:</span>
        </div>
        
        <div className="pill-container">
          {TOP_10_CATEGORIES.map((tag, idx) => (
            <button
              key={idx}
              className={`pill-tag ${userPrompt === tag ? 'selected' : ''}`}
              onClick={() => handlePillClick(tag)}
            >
              {tag}
            </button>
          ))}
        </div>
      </div>

      <div style={{ marginTop: '2.5rem', textAlign: 'center' }}>
        <button
          className="btn btn-primary"
          style={{ padding: '0.85rem 2.5rem', fontSize: '1.05rem', borderRadius: '30px' }}
          onClick={onGenerate}
          disabled={isLoading || photoCount === 0}
        >
          <Sparkles size={18} />
          <span>{isLoading ? "Generating 3 Book Variations..." : "Generate 3 Book Designs"}</span>
        </button>
      </div>
    </div>
  );
}
