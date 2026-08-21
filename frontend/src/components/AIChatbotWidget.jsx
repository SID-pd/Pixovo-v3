import React, { useState, useEffect, useRef } from 'react';
import { Bot, Send, Sparkles, CheckCircle2, Loader2, Compass } from 'lucide-react';

const SUGGESTED_OCCASIONS = [
  "Friends visit to ISKCON Temple",
  "Family Vacation 2025",
  "Birthday Celebration with Loved Ones",
  "Weekend Getaway & Road Trip",
  "College Graduation & Memories",
  "Sacred Spires & Sunset Walk"
];

export default function AIChatbotWidget({
  userPrompt,
  setUserPrompt,
  onGenerate,
  isPhotoUploadComplete,
  uploadedCount,
  isLoading
}) {
  const [messages, setMessages] = useState([
    {
      sender: 'ai',
      text: "Hey there! 👋 I'm analyzing your uploaded photos in the background right now. While I prepare the color palettes & safe bounds, would you like to share the occasion of this album in a short info (~20 words)?"
    }
  ]);
  const [inputVal, setInputVal] = useState(userPrompt || '');
  const chatEndRef = useRef(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = (textToSend) => {
    const text = textToSend || inputVal;
    if (!text.trim()) return;

    setUserPrompt(text);

    const updatedMessages = [
      ...messages,
      { sender: 'user', text: text },
      {
        sender: 'ai',
        text: `Got it! "${text}" sounds like a wonderful memory. Generating 3 custom photobook designs with matching themes & captions...`
      }
    ];

    setMessages(updatedMessages);
    setInputVal('');

    setTimeout(() => {
      onGenerate(text);
    }, 400);
  };

  return (
    <div className="step-card chat-widget-card" style={{ maxWidth: '780px', margin: '0 auto' }}>
      {/* Background Status Indicator */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justify: 'space-between',
        padding: '0.75rem 1.25rem',
        background: isPhotoUploadComplete ? '#ECFDF5' : '#EFF6FF',
        border: `1px solid ${isPhotoUploadComplete ? '#A7F3D0' : '#BFDBFE'}`,
        borderRadius: '14px',
        marginBottom: '1.5rem',
        fontSize: '0.85rem'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          {isPhotoUploadComplete ? (
            <CheckCircle2 size={18} color="#10B981" />
          ) : (
            <Loader2 size={18} color="#3B82F6" className="animate-spin" />
          )}
          <span style={{ fontWeight: 600, color: isPhotoUploadComplete ? '#065F46' : '#1E40AF' }}>
            {isPhotoUploadComplete
              ? `✅ ${uploadedCount} Photos Uploaded & Colors Extracted in Background!`
              : `⚡ Uploading & Downsampling ${uploadedCount} Photos in Background...`}
          </span>
        </div>
        <span style={{ fontSize: '0.75rem', color: '#6B7280', fontWeight: 500 }}>Parallel AI Engine Active</span>
      </div>

      {/* Chat Messages Container */}
      <div className="chat-messages-scroll" style={{
        maxHeight: '280px',
        overflowY: 'auto',
        display: 'flex',
        flexDirection: 'column',
        gap: '1rem',
        paddingRight: '0.5rem',
        marginBottom: '1.5rem'
      }}>
        {messages.map((msg, idx) => (
          <div
            key={idx}
            style={{
              display: 'flex',
              gap: '0.75rem',
              alignSelf: msg.sender === 'user' ? 'flex-end' : 'flex-start',
              maxWidth: '85%'
            }}
          >
            {msg.sender === 'ai' && (
              <div style={{
                width: '36px',
                height: '36px',
                borderRadius: '50%',
                background: 'linear-gradient(135deg, #8B5CF6, #EC4899)',
                display: 'flex',
                alignItems: 'center',
                justify: 'center',
                color: 'white',
                flexShrink: 0
              }}>
                <Bot size={20} />
              </div>
            )}

            <div style={{
              padding: '0.85rem 1.15rem',
              borderRadius: msg.sender === 'user' ? '18px 18px 2px 18px' : '18px 18px 18px 2px',
              background: msg.sender === 'user' ? '#8B5CF6' : '#F3F4F6',
              color: msg.sender === 'user' ? 'white' : '#1F2937',
              fontSize: '0.92rem',
              lineHeight: 1.5,
              boxShadow: '0 2px 8px rgba(0,0,0,0.04)'
            }}>
              {msg.text}
            </div>
          </div>
        ))}
        <div ref={chatEndRef} />
      </div>

      {/* Quick Suggestion Pills */}
      <div style={{ marginBottom: '1.25rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.8rem', color: '#6B7280', marginBottom: '0.5rem' }}>
          <Compass size={14} />
          <span>Quick Occasion Ideas:</span>
        </div>
        <div className="pill-container" style={{ gap: '0.5rem' }}>
          {SUGGESTED_OCCASIONS.map((tag, idx) => (
            <button
              key={idx}
              className="pill-tag"
              style={{ fontSize: '0.8rem', padding: '0.35rem 0.85rem' }}
              onClick={() => handleSend(tag)}
              disabled={isLoading}
            >
              {tag}
            </button>
          ))}
        </div>
      </div>

      {/* Interactive Input Form */}
      <form onSubmit={(e) => { e.preventDefault(); handleSend(); }} style={{ display: 'flex', gap: '0.75rem' }}>
        <input
          type="text"
          value={inputVal}
          onChange={(e) => setInputVal(e.target.value)}
          placeholder="Type occasion details (e.g. Friends visit to Kanpur Temple)..."
          style={{
            flex: 1,
            padding: '0.85rem 1.25rem',
            borderRadius: '24px',
            border: '1px solid #D1D5DB',
            fontSize: '0.95rem',
            outline: 'none',
            fontFamily: 'inherit'
          }}
          disabled={isLoading}
        />
        <button
          type="submit"
          className="btn btn-primary"
          style={{ borderRadius: '24px', padding: '0.85rem 1.5rem' }}
          disabled={isLoading || !inputVal.trim()}
        >
          {isLoading ? (
            <Loader2 size={18} className="animate-spin" />
          ) : (
            <>
              <span>Send & Generate</span>
              <Send size={16} />
            </>
          )}
        </button>
      </form>
    </div>
  );
}
