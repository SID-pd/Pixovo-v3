import React, { useState, useEffect, useRef } from 'react';
import ToolbarHeader from './components/ToolbarHeader';
import PhotoUploader from './components/PhotoUploader';
import AIChatbotWidget from './components/AIChatbotWidget';
import BookCarousel3D from './components/BookCarousel3D';
import SpreadViewer from './components/SpreadViewer';
import BoilerplateInspector from './components/BoilerplateInspector';
import SystemStatsDashboard from './components/SystemStatsDashboard';
import { saveOriginalBlob, getPendingBlobs, removeOriginalBlob } from './utils/indexedDB';
import './styles/storymode.css';

export default function App() {
  const [activeMode, setActiveMode] = useState('story'); // 'story' | 'inspector' | 'stats'
  const [step, setStep] = useState('upload'); // 'upload' -> 'chat' -> 'generating' -> 'preview'
  const [uploadedPhotos, setUploadedPhotos] = useState([]);
  const [isPhotoUploadComplete, setIsPhotoUploadComplete] = useState(false);
  const [uploadedCount, setUploadedCount] = useState(0);
  const [userPrompt, setUserPrompt] = useState('Friends at ISKCON Temple');
  const [currentJobId, setCurrentJobId] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isReshufflingVars, setIsReshufflingVars] = useState(false);
  const [jobProgress, setJobProgress] = useState(0);
  const [jobMessage, setJobMessage] = useState('');
  const [variations, setVariations] = useState([]);
  const [activeVarIdx, setActiveVarIdx] = useState(0);
  const [variationSeedOffset, setVariationSeedOffset] = useState(1);
  const [isExportingPDF, setIsExportingPDF] = useState(false);
  const [syncStatus, setSyncStatus] = useState({ synced: 0, total: 0 });

  const spreadsRef = useRef(null);
  const uploadedPhotosRef = useRef([]);

  // Auto-Resume Unsynced HD Blobs from IndexedDB on startup / reconnect
  useEffect(() => {
    async function resumePendingSync() {
      const pending = await getPendingBlobs();
      if (pending && pending.length > 0) {
        console.log(`[IndexedDB Auto-Resume] Found ${pending.length} unsynced HD blobs. Resuming background upload...`);
        setSyncStatus({ synced: 0, total: pending.length });
        let count = 0;
        for (const item of pending) {
          try {
            const formData = new FormData();
            formData.append('file', item.blob, item.filename);

            const res = await fetch(`/api/upload-originals?photo_id=${item.photoId}`, {
              method: 'POST',
              body: formData
            });

            if (res.ok) {
              await removeOriginalBlob(item.photoId);
              count++;
              setSyncStatus({ synced: count, total: pending.length });
              console.log(`[IndexedDB Auto-Resume] Successfully uploaded & cleaned up ${item.photoId}`);
            }
          } catch (err) {
            console.warn(`[IndexedDB Auto-Resume] Network offline, will retry later for ${item.photoId}:`, err);
          }
        }
      }
    }

    resumePendingSync();
    window.addEventListener('online', resumePendingSync);
    return () => window.removeEventListener('online', resumePendingSync);
  }, []);

  // Background HD Original Asset Sync Queue with IndexedDB Persistence
  const streamOriginalsInBackground = async (originalFilesMap) => {
    const photoIds = Object.keys(originalFilesMap);
    const total = photoIds.length;
    if (total === 0) return;

    setSyncStatus({ synced: 0, total });
    let syncedCount = 0;

    // Concurrency pool of 3 parallel streams
    const concurrency = 3;
    for (let i = 0; i < photoIds.length; i += concurrency) {
      const chunk = photoIds.slice(i, i + concurrency);
      await Promise.all(chunk.map(async (photoId) => {
        const file = originalFilesMap[photoId];
        if (!file) return;

        // 1. Immediately persist original file blob to IndexedDB
        await saveOriginalBlob(photoId, file);

        try {
          const formData = new FormData();
          formData.append('file', file);

          const res = await fetch(`/api/upload-originals?photo_id=${photoId}`, {
            method: 'POST',
            body: formData
          });

          if (res.ok) {
            await removeOriginalBlob(photoId);
            syncedCount++;
            setSyncStatus({ synced: syncedCount, total });
            console.log(`[Background Sync] Synced original HD file for ${photoId} (${syncedCount}/${total})`);
          }
        } catch (err) {
          console.warn(`[Background Sync] Will retry later for ${photoId}:`, err);
        }
      }));
    }
  };

  // Triggered after Phase 1 client-side downsampling completes
  const handlePhotosUploaded = async (uploadPackage) => {
    const { processedCount, processedPhotos, previewItems, formData, downsampleTimeMs } = uploadPackage;
    setUploadedCount(processedCount);
    setIsPhotoUploadComplete(false);
    setStep('chat'); // Immediately show AI Chatbot / Theme Selector in foreground!

    // Record Client-side Downsample telemetry to backend metrics
    if (downsampleTimeMs) {
      try {
        fetch('/api/client-metrics', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            step_name: 'Client Browser Downsampling (512px)',
            elapsed_ms: downsampleTimeMs,
            details: { total_photos: processedCount }
          })
        }).catch(() => {});
      } catch (_) {}
    }

    // Build raw original files mapping for background stream
    const origMap = {};
    if (processedPhotos) {
      processedPhotos.forEach(p => {
        if (p.photo_id && p.original_file) {
          origMap[p.photo_id] = p.original_file;
        }
      });
    }

    try {
      // POST Phase 1 Dual-Payload (512px Thumbnails + Metadata)
      const res = await fetch('/api/photobook/ingest', {
        method: 'POST',
        body: formData
      });

      if (res.ok) {
        const data = await res.json();
        const uploadedMetas = data.photos || [];
        uploadedPhotosRef.current = uploadedMetas;
        setUploadedPhotos(uploadedMetas);
        setIsPhotoUploadComplete(true);
        console.log(`[Phase 1 Ingestion] Successfully ingested ${data.total_ingested} photos. Session ID: ${data.session_id}`);

        // Launch background original HD streaming without blocking UI!
        streamOriginalsInBackground(origMap);
      } else {
        let errDetail = 'Failed to ingest photos';
        try {
          const errData = await res.json();
          errDetail = errData.detail || errDetail;
        } catch (_) {
          errDetail = `Server returned status ${res.status} (${res.statusText})`;
        }
        alert(`Ingestion error: ${errDetail}`);
        setStep('upload');
      }
    } catch (e) {
      console.error("[Phase 1 Ingestion] Network or server error:", e);
      setIsPhotoUploadComplete(true);
    }
  };

  const handleGenerateVariationsAsync = async (promptOverride) => {
    // Wait for in-flight Phase 1 ingestion upload to complete if photos were selected
    let photosToUse = uploadedPhotos.length > 0 ? uploadedPhotos : uploadedPhotosRef.current;
    if (photosToUse.length === 0 && uploadedCount > 0) {
      for (let i = 0; i < 40; i++) { // wait up to 8 seconds for ingestion
        if (uploadedPhotosRef.current.length > 0) {
          photosToUse = uploadedPhotosRef.current;
          break;
        }
        await new Promise(r => setTimeout(r, 200));
      }
    }

    const promptToUse = promptOverride || userPrompt;
    setIsLoading(true);
    setStep('generating');
    setJobProgress(10);
    setJobMessage("Submitting async photobook job...");

    try {
      const photoIds = photosToUse.map(p => p.id);
      console.log(`[Generate Async] Submitting ${photoIds.length} photo IDs for prompt: '${promptToUse}'`);
      const res = await fetch('/api/generate-async', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          photo_ids: photoIds,
          user_prompt: promptToUse
        })
      });

      if (res.status === 202) {
        const job = await res.json();
        setCurrentJobId(job.job_id);
        pollJobStatus(job.job_id);
      } else {
        alert("Failed to submit job");
        setIsLoading(false);
        setStep('chat');
      }
    } catch (e) {
      console.error("Generation error:", e);
      setIsLoading(false);
      setStep('chat');
    }
  };

  const pollJobStatus = (jobId) => {
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`/api/jobs/${jobId}`);
        if (res.ok) {
          const job = await res.json();
          setJobProgress(job.progress || 0);
          setJobMessage(job.message || "Processing...");

          if (job.status === "completed" && job.result) {
            clearInterval(interval);
            setVariations(job.result.variations || []);
            setActiveVarIdx(0);
            setIsLoading(false);
            setStep('preview');
          } else if (job.status === "failed") {
            clearInterval(interval);
            alert(`Generation failed: ${job.message}`);
            setIsLoading(false);
            setStep('chat');
          }
        }
      } catch (err) {
        console.error("Polling error:", err);
      }
    }, 500);
  };

  const handleSpreadUpdate = (spreadIdx, newSpread) => {
    setVariations(prevVars => {
      const updated = [...prevVars];
      const activeVar = { ...updated[activeVarIdx] };
      const updatedSpreads = [...activeVar.spreads];
      updatedSpreads[spreadIdx] = newSpread;
      activeVar.spreads = updatedSpreads;
      updated[activeVarIdx] = activeVar;
      return updated;
    });
  };

  const handleReshuffleVariations = async () => {
    if (!currentJobId) return;
    setIsReshufflingVars(true);
    const nextOffset = variationSeedOffset + 1;
    setVariationSeedOffset(nextOffset);

    try {
      const res = await fetch('/api/variations/reshuffle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          job_id: currentJobId,
          seed_offset: nextOffset
        })
      });

      if (res.ok) {
        const result = await res.json();
        setVariations(result.variations || []);
      }
    } catch (err) {
      console.error("Variations reshuffle error:", err);
    } finally {
      setIsReshufflingVars(false);
    }
  };

  const handleExportPDF = async () => {
    const selectedVar = variations[activeVarIdx];
    if (!selectedVar) {
      alert("No photobook variation selected for PDF export.");
      return;
    }

    setIsExportingPDF(true);
    try {
      const res = await fetch('/api/export-pdf', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          variation: selectedVar,
          page_width_mm: 200,
          page_height_mm: 200,
          bleed_mm: 3,
          dpi: 300
        })
      });

      if (res.ok) {
        const data = await res.json();
        if (data.pdf_url) {
          const downloadUrl = data.pdf_url.startsWith('http') ? data.pdf_url : `${window.location.origin}${data.pdf_url}`;
          const link = document.createElement('a');
          link.href = downloadUrl;
          link.target = '_blank';
          link.download = data.filename || 'pixovo_print_300dpi.pdf';
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
        } else {
          alert("PDF compiled successfully!");
        }
      } else {
        const err = await res.json();
        alert(`PDF Export Error: ${err.detail || 'Failed to compile PDF'}`);
      }
    } catch (e) {
      console.error("PDF Export Error:", e);
      alert("PDF Export failed. Check backend log.");
    } finally {
      setIsExportingPDF(false);
    }
  };

  const scrollToSpreads = () => {
    if (spreadsRef.current) {
      spreadsRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  };

  return (
    <div className="app-container">
      <ToolbarHeader 
        activeMode={activeMode} 
        setActiveMode={setActiveMode}
        onExportPDF={step === 'preview' ? handleExportPDF : null}
        isExporting={isExportingPDF}
        syncStatus={syncStatus}
      />

      <main className="main-wrapper">
        {activeMode === 'inspector' && (
          <BoilerplateInspector />
        )}

        {activeMode === 'stats' && (
          <SystemStatsDashboard />
        )}

        {activeMode === 'story' && (
          <>
            {step === 'generating' && (
              <div className="step-card" style={{ textAlign: 'center', padding: '3rem' }}>
                <h3 style={{ marginBottom: '1rem', color: '#1F2937' }}>{jobMessage}</h3>
                <div style={{
                  width: '100%',
                  height: '10px',
                  background: '#E5E7EB',
                  borderRadius: '5px',
                  overflow: 'hidden',
                  marginTop: '1.5rem'
                }}>
                  <div style={{
                    width: `${jobProgress}%`,
                    height: '100%',
                    background: 'linear-gradient(90deg, #8B5CF6, #EC4899)',
                    transition: 'width 0.3s ease'
                  }} />
                </div>
                <p style={{ marginTop: '0.75rem', color: '#6B7280', fontSize: '0.9rem' }}>{jobProgress}% completed</p>
              </div>
            )}

            {step === 'upload' && (
              <PhotoUploader 
                onPhotosUploaded={handlePhotosUploaded} 
                isUploading={isLoading} 
              />
            )}

            {step === 'chat' && (
              <AIChatbotWidget
                userPrompt={userPrompt}
                setUserPrompt={setUserPrompt}
                onGenerate={handleGenerateVariationsAsync}
                isPhotoUploadComplete={isPhotoUploadComplete}
                uploadedCount={uploadedCount}
                isLoading={isLoading}
              />
            )}

            {step === 'preview' && (
              <div className="story-preview-container">
                <BookCarousel3D
                  variations={variations}
                  activeIdx={activeVarIdx}
                  setActiveIdx={setActiveVarIdx}
                  onScrollDown={scrollToSpreads}
                  onReshuffleVariations={handleReshuffleVariations}
                  isReshuffling={isReshufflingVars}
                />

                <SpreadViewer
                  selectedVariation={variations[activeVarIdx]}
                  targetRef={spreadsRef}
                  onSpreadUpdate={handleSpreadUpdate}
                />
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
