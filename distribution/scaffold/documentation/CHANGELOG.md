# Public workflow changelog

## 1.2.3 candidate — unpublished

Fix opt-in comment replies when live comments.list omits videoId. Require an exact live commentThreads match for thread ID, top comment ID/text, own channel, video, public state and canReply, plus a fresh public-video check; reject self-comments. Existing parent comments with videoId retain the direct route without a thread API call. Preserve exact approved reply text, uncertain-reply deduplication and 403 rejection. No new parameters, providers or schema. All optional modules remain OFF by default; all 1.2.2 caption, concat, frame mask, installer font and author-route behavior is retained.

## 1.2.2 — published

Normal pilot publication, repeated readback, recovery and final privacy verification make zero captions.download calls. Preserve local five-track quality/hash review, successful nonempty caption-ID receipts and live video/language/name/serving checks. Processing waits, failed/malformed/unknown remain gated; retries never blindly resend uncertain uploads. Report upload_receipt_verified and metadata_serving_verified with content_matches=null. Explicit human --diagnostic-caption-download is the only full-text route and performs fresh remote cue comparison. Legacy successful ID-only receipts and earlier full-text evidence remain compatible. No private scheduler or channel defaults are exported.

The 1.2.1 decoder-context concat, exact typed chapter-caption mask and explicit installer font path/family/index contract remain intact. All optional learning modules stay OFF and configurable author routing is preserved. No real media, voice, fonts, credentials, accounts or authorization are included.
