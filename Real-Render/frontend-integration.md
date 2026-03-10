# Real-Render Frontend Integration Guide

> **Paste these files into your Lovable.dev project to connect the React frontend to the FastAPI backend.**

---

## Important: Tunnel URL & Stripe Redirect Configuration

The backend runs behind a Cloudflare quick tunnel. The tunnel URL rotates on every restart.

**When the tunnel URL changes, update TWO things:**

1. `API_BASE` in `src/lib/api.ts` (below)
2. `PUBLIC_BASE_URL` in the backend `.env` -- this MUST be set to your **Lovable frontend domain** (`https://opendoorcinematic.com`) so that Stripe redirects land on your React app, not the tunnel

---

## 1. API Client -- `src/lib/api.ts`

```typescript
// src/lib/api.ts

// Update this when the Cloudflare quick tunnel rotates
const API_BASE = "https://views-rica-elections-deals.trycloudflare.com";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface CheckoutResponse {
  checkout_url: string;
  job_id: string;
  total_price_usd: number;
}

export interface JobProgress {
  id: string;
  status: string;
  current_phase: string | null;
  strategy: string | null;
  steps: Array<{
    provider: string;
    phase: string;
    ok: boolean;
    result: Record<string, unknown>;
  }>;
  scores: Record<string, {
    score: number;
    width?: number;
    height?: number;
    duration_secs?: number;
    file_size_bytes?: number;
  }>;
  winner: string | null;
  total_cost_usd: number;
}

export interface ArtifactLink {
  filename: string;
  download_url: string;
}

export interface JobDetail {
  id: string;
  status: string;
  created_at: string;
  updated_at: string;
  customer_ref: string | null;
  package: string | null;
  email: string | null;
  rooms: number;
  total_price_usd: number;
  options: Record<string, unknown>;
  qc: Record<string, unknown>;
  error: string | null;
  artifacts: ArtifactLink[];
}

// ---------------------------------------------------------------------------
// API Functions
// ---------------------------------------------------------------------------

/**
 * Create a Stripe Checkout session.
 * Returns the hosted checkout URL, the new job ID, and total price.
 */
export async function createCheckout(
  email: string,
  pkg: string,
  rooms: number,
  addons: string[],
  customerRef?: string
): Promise<CheckoutResponse> {
  const body: Record<string, unknown> = {
    email,
    package: pkg,
    rooms,
    addons,
  };
  if (customerRef) {
    body.customer_ref = customerRef;
  }

  const res = await fetch(`${API_BASE}/api/v1/checkout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Checkout failed (${res.status})`);
  }

  return res.json();
}

/**
 * Upload property photos (ZIP) for a job that has already been paid for.
 */
export async function uploadPhotos(
  jobId: string,
  zipFile: File
): Promise<JobDetail> {
  const form = new FormData();
  form.append("zip_file", zipFile);

  const res = await fetch(`${API_BASE}/api/v1/jobs/${jobId}/upload`, {
    method: "POST",
    body: form,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Upload failed (${res.status})`);
  }

  return res.json();
}

/**
 * Poll the current progress of a job.
 * Returns phase info, scores, and winner.
 */
export async function getJobProgress(jobId: string): Promise<JobProgress> {
  const res = await fetch(`${API_BASE}/api/v1/jobs/${jobId}/progress`);

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Progress fetch failed (${res.status})`);
  }

  return res.json();
}

/**
 * Fetch full job details, including artifact download URLs.
 */
export async function getJobDetail(jobId: string): Promise<JobDetail> {
  const res = await fetch(`${API_BASE}/api/v1/jobs/${jobId}`);

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Job detail fetch failed (${res.status})`);
  }

  return res.json();
}
```

---

## 2. Checkout Flow -- integrate into your existing pricing page

Add this handler to whichever component contains your pricing form. It collects the selected options and redirects the user to Stripe.

```typescript
// Inside your pricing page component, e.g. src/pages/Pricing.tsx

import { useState } from "react";
import { createCheckout } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useToast } from "@/hooks/use-toast";

// Example: wire this into your existing form state
interface CheckoutFormState {
  email: string;
  package: "essential" | "signature" | "premium";
  rooms: number;
  addons: string[];        // e.g. ["rush_delivery", "custom_staging"]
  customerRef?: string;    // optional MLS or listing ID
}

function useCheckoutHandler() {
  const [loading, setLoading] = useState(false);
  const { toast } = useToast();

  const handleCheckout = async (form: CheckoutFormState) => {
    if (!form.email) {
      toast({
        title: "Email required",
        description: "Please enter your email address to continue.",
        variant: "destructive",
      });
      return;
    }

    setLoading(true);
    try {
      const { checkout_url, job_id, total_price_usd } = await createCheckout(
        form.email,
        form.package,
        form.rooms,
        form.addons,
        form.customerRef
      );

      // Store job_id in localStorage so the success page can find it
      // (Stripe also passes it back via query param, but this is a fallback)
      localStorage.setItem("rr_pending_job_id", job_id);

      // Redirect to Stripe hosted checkout
      window.location.href = checkout_url;
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Something went wrong";
      toast({
        title: "Checkout failed",
        description: message,
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  };

  return { handleCheckout, loading };
}
```

### Example checkout button (drop into your pricing card):

```tsx
// Inside each pricing card's action area:

const { handleCheckout, loading } = useCheckoutHandler();

<Button
  className="w-full"
  size="lg"
  disabled={loading}
  onClick={() =>
    handleCheckout({
      email,            // from your form state
      package: "signature",
      rooms: roomCount,
      addons: selectedAddons,
      customerRef: mlsNumber || undefined,
    })
  }
>
  {loading ? "Redirecting to payment..." : "Get Started"}
</Button>
```

---

## 3. Success Page -- `src/pages/CheckoutSuccess.tsx`

After Stripe payment completes, the user is redirected to:
```
https://opendoorcinematic.com/checkout/success?session_id=cs_xxx&job_id=abc123
```

This page shows a confirmation message and a file upload dropzone for the property photos.

### Add the route in your router:

```tsx
// In your router config (e.g. src/App.tsx or src/routes.tsx)
import CheckoutSuccess from "@/pages/CheckoutSuccess";

// Add this route:
<Route path="/checkout/success" element={<CheckoutSuccess />} />
<Route path="/checkout/cancel" element={<CheckoutCancel />} />
<Route path="/order/:jobId" element={<OrderTracking />} />
```

### The success page component:

```tsx
// src/pages/CheckoutSuccess.tsx

import { useState, useCallback } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { uploadPhotos } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { useToast } from "@/hooks/use-toast";
import { CheckCircle, Upload, FileArchive, ArrowRight } from "lucide-react";

export default function CheckoutSuccess() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { toast } = useToast();

  const jobId = searchParams.get("job_id") || localStorage.getItem("rr_pending_job_id") || "";
  const sessionId = searchParams.get("session_id") || "";

  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploaded, setUploaded] = useState(false);

  const handleDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    const droppedFile = e.dataTransfer.files[0];
    if (droppedFile && droppedFile.name.endsWith(".zip")) {
      setFile(droppedFile);
    } else {
      toast({
        title: "Invalid file",
        description: "Please upload a .zip file containing your property photos.",
        variant: "destructive",
      });
    }
  }, [toast]);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0];
    if (selected) {
      setFile(selected);
    }
  };

  const handleUpload = async () => {
    if (!file || !jobId) return;

    setUploading(true);
    setUploadProgress(20);

    try {
      setUploadProgress(50);
      await uploadPhotos(jobId, file);
      setUploadProgress(100);
      setUploaded(true);

      // Clear the stored job ID
      localStorage.removeItem("rr_pending_job_id");

      toast({
        title: "Photos uploaded!",
        description: "Your order is now being processed. Redirecting to tracking...",
      });

      // Redirect to order tracking after a short delay
      setTimeout(() => {
        navigate(`/order/${jobId}`);
      }, 2000);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Upload failed";
      toast({
        title: "Upload failed",
        description: message,
        variant: "destructive",
      });
    } finally {
      setUploading(false);
    }
  };

  if (!jobId) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <Card className="max-w-md w-full">
          <CardContent className="pt-6 text-center">
            <p className="text-muted-foreground">
              No order found. Please start from the{" "}
              <a href="/pricing" className="underline text-primary">pricing page</a>.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4 bg-muted/30">
      <Card className="max-w-lg w-full">
        <CardHeader className="text-center">
          <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-green-100">
            <CheckCircle className="h-8 w-8 text-green-600" />
          </div>
          <CardTitle className="text-2xl">Payment Successful!</CardTitle>
          <CardDescription>
            Order <Badge variant="outline" className="ml-1 font-mono">{jobId}</Badge>
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-6">
          {!uploaded ? (
            <>
              <p className="text-center text-muted-foreground">
                Now upload your property photos so we can start rendering.
                Package them as a single <strong>.zip</strong> file.
              </p>

              {/* Dropzone */}
              <div
                onDrop={handleDrop}
                onDragOver={(e) => e.preventDefault()}
                className="border-2 border-dashed rounded-lg p-8 text-center cursor-pointer hover:border-primary/50 transition-colors"
                onClick={() => document.getElementById("file-input")?.click()}
              >
                <input
                  id="file-input"
                  type="file"
                  accept=".zip"
                  className="hidden"
                  onChange={handleFileSelect}
                />

                {file ? (
                  <div className="flex items-center justify-center gap-3">
                    <FileArchive className="h-8 w-8 text-primary" />
                    <div className="text-left">
                      <p className="font-medium">{file.name}</p>
                      <p className="text-sm text-muted-foreground">
                        {(file.size / (1024 * 1024)).toFixed(1)} MB
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-2">
                    <Upload className="h-10 w-10 mx-auto text-muted-foreground" />
                    <p className="font-medium">Drop your .zip file here</p>
                    <p className="text-sm text-muted-foreground">
                      or click to browse
                    </p>
                  </div>
                )}
              </div>

              {uploading && (
                <Progress value={uploadProgress} className="w-full" />
              )}

              <Button
                className="w-full"
                size="lg"
                disabled={!file || uploading}
                onClick={handleUpload}
              >
                {uploading ? "Uploading..." : "Upload Photos & Start Rendering"}
              </Button>
            </>
          ) : (
            <div className="text-center space-y-4">
              <p className="text-green-600 font-medium">
                Photos uploaded successfully!
              </p>
              <p className="text-sm text-muted-foreground">
                Redirecting to your order tracking page...
              </p>
              <Button
                variant="outline"
                onClick={() => navigate(`/order/${jobId}`)}
              >
                Go to Order Tracking <ArrowRight className="ml-2 h-4 w-4" />
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
```

---

## 4. Checkout Cancel Page -- `src/pages/CheckoutCancel.tsx`

```tsx
// src/pages/CheckoutCancel.tsx

import { useSearchParams } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { XCircle } from "lucide-react";

export default function CheckoutCancel() {
  const [searchParams] = useSearchParams();
  const jobId = searchParams.get("job_id") || "";

  return (
    <div className="min-h-screen flex items-center justify-center p-4 bg-muted/30">
      <Card className="max-w-md w-full">
        <CardHeader className="text-center">
          <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-red-100">
            <XCircle className="h-8 w-8 text-red-600" />
          </div>
          <CardTitle className="text-xl">Payment Cancelled</CardTitle>
        </CardHeader>
        <CardContent className="text-center space-y-4">
          <p className="text-muted-foreground">
            Your payment was not completed. No charges were made.
          </p>
          <Button asChild className="w-full">
            <a href="/pricing">Return to Pricing</a>
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
```

---

## 5. Order Tracking Component -- `src/pages/OrderTracking.tsx`

This component polls the backend every 10 seconds and shows a real-time progress view through the pipeline phases.

```tsx
// src/pages/OrderTracking.tsx

import { useEffect, useState, useRef } from "react";
import { useParams } from "react-router-dom";
import { getJobProgress, getJobDetail } from "@/lib/api";
import type { JobProgress, JobDetail, ArtifactLink } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Button } from "@/components/ui/button";
import { Download, CheckCircle, Loader2, AlertCircle, Trophy } from "lucide-react";

// ---------------------------------------------------------------------------
// Phase configuration
// ---------------------------------------------------------------------------

const PHASE_ORDER = ["compete", "evaluate", "refine", "finalize", "done"];

function phaseToIndex(phase: string | null, status: string): number {
  if (status === "done") return 4;
  if (status === "error") return -1;
  if (!phase) return 0;
  if (phase === "compete") return 0;
  if (phase === "evaluate") return 1;
  if (phase.startsWith("refine")) return 2;
  if (phase === "finalize") return 3;
  return 0;
}

function phaseLabel(phase: string | null, status: string): string {
  if (status === "done") return "Complete";
  if (status === "error") return "Error";
  if (status === "pending_payment") return "Awaiting Payment";
  if (status === "queued") return "Queued";
  if (!phase) return "Starting...";
  if (phase === "compete") return "Generating Renders";
  if (phase === "evaluate") return "Evaluating Quality";
  if (phase.startsWith("refine")) return "Refining Output";
  if (phase === "finalize") return "Finalizing Deliverables";
  return phase;
}

function progressPercent(phase: string | null, status: string): number {
  if (status === "done") return 100;
  if (status === "error") return 100;
  if (status === "pending_payment" || status === "queued") return 0;
  const idx = phaseToIndex(phase, status);
  // 5 phases: 0..4, map to 10..100
  return Math.max(10, Math.round(((idx + 0.5) / 5) * 100));
}

function statusBadgeVariant(status: string): "default" | "secondary" | "destructive" | "outline" {
  switch (status) {
    case "done": return "default";
    case "error": return "destructive";
    case "processing": return "secondary";
    default: return "outline";
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function OrderTracking() {
  const { jobId } = useParams<{ jobId: string }>();
  const [progress, setProgress] = useState<JobProgress | null>(null);
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!jobId) return;

    const poll = async () => {
      try {
        const prog = await getJobProgress(jobId);
        setProgress(prog);
        setError(null);

        // When done or error, fetch full detail (for artifact links) and stop polling
        if (prog.status === "done" || prog.status === "error") {
          const det = await getJobDetail(jobId);
          setDetail(det);
          if (intervalRef.current) {
            clearInterval(intervalRef.current);
            intervalRef.current = null;
          }
        }
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : "Failed to fetch progress";
        setError(message);
      }
    };

    // Initial fetch
    poll();

    // Poll every 10 seconds
    intervalRef.current = setInterval(poll, 10_000);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
    };
  }, [jobId]);

  if (!jobId) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <p className="text-muted-foreground">No order ID provided.</p>
      </div>
    );
  }

  if (error && !progress) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <Card className="max-w-md w-full">
          <CardContent className="pt-6 text-center space-y-2">
            <AlertCircle className="h-8 w-8 mx-auto text-destructive" />
            <p className="font-medium">Unable to load order</p>
            <p className="text-sm text-muted-foreground">{error}</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!progress) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const phase = progress.current_phase;
  const status = progress.status;
  const pct = progressPercent(phase, status);
  const isDone = status === "done";
  const isError = status === "error";

  return (
    <div className="min-h-screen flex items-center justify-center p-4 bg-muted/30">
      <div className="max-w-2xl w-full space-y-6">

        {/* Header */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="text-2xl">Order Tracking</CardTitle>
                <CardDescription className="font-mono mt-1">{jobId}</CardDescription>
              </div>
              <Badge variant={statusBadgeVariant(status)}>
                {status === "processing" ? phaseLabel(phase, status) : status}
              </Badge>
            </div>
          </CardHeader>
          <CardContent>
            <Progress value={pct} className="w-full h-3" />
            <p className="text-sm text-muted-foreground mt-2">
              {phaseLabel(phase, status)}
              {progress.strategy && status === "processing" && (
                <span className="ml-2 text-xs">
                  (strategy: {progress.strategy})
                </span>
              )}
            </p>
          </CardContent>
        </Card>

        {/* Phase timeline */}
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Pipeline Progress</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {PHASE_ORDER.map((p, i) => {
                const currentIdx = phaseToIndex(phase, status);
                const isActive = i === currentIdx && status === "processing";
                const isComplete = isDone ? true : i < currentIdx;
                const isFuture = !isDone && i > currentIdx;

                return (
                  <div key={p} className="flex items-center gap-3">
                    <div className={`flex h-8 w-8 items-center justify-center rounded-full border-2 text-sm font-medium
                      ${isComplete ? "bg-primary border-primary text-primary-foreground" : ""}
                      ${isActive ? "border-primary text-primary animate-pulse" : ""}
                      ${isFuture ? "border-muted text-muted-foreground" : ""}
                      ${isError && isActive ? "border-destructive text-destructive" : ""}
                    `}>
                      {isComplete ? (
                        <CheckCircle className="h-4 w-4" />
                      ) : isActive ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        i + 1
                      )}
                    </div>
                    <span className={`text-sm ${isActive ? "font-medium" : ""} ${isFuture ? "text-muted-foreground" : ""}`}>
                      {p === "compete" && "Generate Renders"}
                      {p === "evaluate" && "Evaluate Quality"}
                      {p === "refine" && "Refine Output"}
                      {p === "finalize" && "Finalize Deliverables"}
                      {p === "done" && "Complete"}
                    </span>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>

        {/* Scores (visible during evaluate/refine or after done) */}
        {Object.keys(progress.scores).length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Quality Scores</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {Object.entries(progress.scores).map(([provider, info]) => (
                  <div
                    key={provider}
                    className={`p-4 rounded-lg border ${
                      provider === progress.winner ? "border-primary bg-primary/5" : ""
                    }`}
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className="font-medium capitalize">{provider}</span>
                      {provider === progress.winner && (
                        <Trophy className="h-4 w-4 text-primary" />
                      )}
                    </div>
                    <p className="text-2xl font-bold">{info.score.toFixed(1)}</p>
                    {info.width && info.height && (
                      <p className="text-xs text-muted-foreground mt-1">
                        {info.width}x{info.height}
                        {info.duration_secs ? ` / ${info.duration_secs.toFixed(1)}s` : ""}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}

        {/* Error state */}
        {isError && detail?.error && (
          <Card className="border-destructive">
            <CardContent className="pt-6">
              <div className="flex items-start gap-3">
                <AlertCircle className="h-5 w-5 text-destructive mt-0.5" />
                <div>
                  <p className="font-medium text-destructive">Processing Error</p>
                  <p className="text-sm text-muted-foreground mt-1">{detail.error}</p>
                </div>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Download links (when done) */}
        {isDone && detail && detail.artifacts.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Your Deliverables</CardTitle>
              <CardDescription>
                Click to download your rendered files.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                {detail.artifacts.map((artifact: ArtifactLink) => (
                  <a
                    key={artifact.filename}
                    href={artifact.download_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center justify-between p-3 rounded-lg border hover:bg-muted/50 transition-colors"
                  >
                    <span className="font-mono text-sm truncate">
                      {artifact.filename}
                    </span>
                    <Button variant="ghost" size="sm">
                      <Download className="h-4 w-4 mr-1" />
                      Download
                    </Button>
                  </a>
                ))}
              </div>
            </CardContent>
          </Card>
        )}

        {isDone && detail && detail.artifacts.length === 0 && (
          <Card>
            <CardContent className="pt-6 text-center text-muted-foreground">
              Processing complete but no artifacts were generated.
              Please contact support.
            </CardContent>
          </Card>
        )}

      </div>
    </div>
  );
}
```

---

## 6. Reusable Order Tracking Widget -- `src/components/OrderTracker.tsx`

If you want to embed the tracker inside another page (e.g., a dashboard) rather than as a standalone page, use this wrapper:

```tsx
// src/components/OrderTracker.tsx

import { useEffect, useState, useRef } from "react";
import { getJobProgress, getJobDetail } from "@/lib/api";
import type { JobProgress, JobDetail } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Loader2 } from "lucide-react";

interface OrderTrackerProps {
  jobId: string;
  pollIntervalMs?: number;
  onComplete?: (detail: JobDetail) => void;
}

export function OrderTracker({
  jobId,
  pollIntervalMs = 10_000,
  onComplete,
}: OrderTrackerProps) {
  const [progress, setProgress] = useState<JobProgress | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const poll = async () => {
      try {
        const prog = await getJobProgress(jobId);
        setProgress(prog);

        if (prog.status === "done" || prog.status === "error") {
          if (intervalRef.current) {
            clearInterval(intervalRef.current);
            intervalRef.current = null;
          }
          if (prog.status === "done" && onComplete) {
            const detail = await getJobDetail(jobId);
            onComplete(detail);
          }
        }
      } catch {
        // Silently retry on next interval
      }
    };

    poll();
    intervalRef.current = setInterval(poll, pollIntervalMs);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [jobId, pollIntervalMs, onComplete]);

  if (!progress) {
    return <Loader2 className="h-5 w-5 animate-spin" />;
  }

  const pct =
    progress.status === "done"
      ? 100
      : progress.status === "error"
        ? 100
        : progress.current_phase === "compete"
          ? 20
          : progress.current_phase === "evaluate"
            ? 40
            : progress.current_phase?.startsWith("refine")
              ? 60
              : progress.current_phase === "finalize"
                ? 80
                : 5;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-sm">
        <span className="font-mono">{jobId}</span>
        <Badge variant={progress.status === "done" ? "default" : progress.status === "error" ? "destructive" : "secondary"}>
          {progress.status}
        </Badge>
      </div>
      <Progress value={pct} className="h-2" />
      <p className="text-xs text-muted-foreground">
        {progress.current_phase || progress.status}
        {progress.winner && ` — winner: ${progress.winner}`}
      </p>
    </div>
  );
}
```

---

## 7. Router Setup Summary

Add these routes to your React Router configuration:

```tsx
// src/App.tsx (or wherever your routes live)

import { BrowserRouter, Routes, Route } from "react-router-dom";
import CheckoutSuccess from "@/pages/CheckoutSuccess";
import CheckoutCancel from "@/pages/CheckoutCancel";
import OrderTracking from "@/pages/OrderTracking";
// ...your existing imports

function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* ...existing routes like / and /pricing */}
        <Route path="/checkout/success" element={<CheckoutSuccess />} />
        <Route path="/checkout/cancel" element={<CheckoutCancel />} />
        <Route path="/order/:jobId" element={<OrderTracking />} />
      </Routes>
    </BrowserRouter>
  );
}
```

---

## 8. Backend Configuration Checklist

Before testing the integration end-to-end:

1. **Set `PUBLIC_BASE_URL`** in the backend `.env` to your Lovable frontend domain:
   ```
   PUBLIC_BASE_URL=https://opendoorcinematic.com
   ```
   This ensures Stripe redirects back to the React app (not the tunnel).

2. **Set Stripe keys** in the backend `.env`:
   ```
   STRIPE_SECRET_KEY=sk_test_...
   STRIPE_PUBLISHABLE_KEY=pk_test_...
   STRIPE_WEBHOOK_SECRET=whsec_...
   ```

3. **Configure Stripe webhook** in the Stripe Dashboard to point to:
   ```
   https://<tunnel-url>/api/v1/webhooks/stripe
   ```
   Listen for the `checkout.session.completed` event.

4. **CORS** is configured to allow `*` by default. For production, set:
   ```
   CORS_ALLOWED_ORIGINS=https://opendoorcinematic.com
   ```

---

## 9. Available Add-on Keys

Use these exact string values in the `addons` array:

| Key                  | Display Name             | Price   |
|----------------------|--------------------------|---------|
| `rush_delivery`      | Rush Delivery            | $140.00 |
| `extra_revision`     | Extra Revision           | $35.00  |
| `custom_staging`     | Custom Virtual Staging   | $70.00  |
| `instagram_carousel` | Instagram Carousel       | $35.00  |
| `unique_request`     | Custom Request           | $0.00   |

## 10. Package Pricing Reference

| Package     | Base Price | Extra Room Rate |
|-------------|-----------|-----------------|
| `essential` | $79.00    | $20.00/room     |
| `signature` | $139.00   | $30.00/room     |
| `premium`   | $199.00   | $40.00/room     |

---

## Flow Diagram

```
User on Pricing Page
  |
  v
[Select package, rooms, addons, enter email]
  |
  v
createCheckout() --> POST /api/v1/checkout
  |
  v
Redirect to Stripe hosted checkout (checkout_url)
  |
  v
Stripe processes payment
  |
  v
Stripe webhook --> POST /api/v1/webhooks/stripe
  (backend moves job from pending_payment to queued)
  |
  v
Stripe redirects user to /checkout/success?session_id=...&job_id=...
  |
  v
User uploads .zip of property photos
  |
  v
uploadPhotos() --> POST /api/v1/jobs/{id}/upload
  (backend extracts photos, enqueues pipeline)
  |
  v
Redirect to /order/{jobId}
  |
  v
Poll getJobProgress() every 10s
  compete -> evaluate -> refine -> finalize -> done
  |
  v
Show download links from getJobDetail()
```
