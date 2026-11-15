import type { Room } from 'livekit-client';

interface StreamInfo {
  id: string;
  attributes?: Record<string, string>;
}

/**
 * Who spoke: LiveKit's `lk.transcribed_track_id` names the audio track the
 * segment came from, so a segment is the user's only when that track id is one
 * this participant published. Publisher identity is not a speaker signal.
 */
export function speakerForTranscribedTrack(room: Room, transcribedTrackId: string | undefined): 'user' | 'assistant' {
  if (!transcribedTrackId) return 'assistant';
  for (const publication of room.localParticipant.trackPublications.values()) {
    if (publication.trackSid === transcribedTrackId) return 'user';
  }
  return 'assistant';
}

/**
 * Map a `lk.transcription` text-stream header onto transcript fields. Older
 * publishers omit the final flag; treat an omitted flag as final but never
 * force a provisional stream into a final assistant turn.
 */
export function transcriptMetadata(room: Room, info: StreamInfo) {
  const attributes = info.attributes ?? {};
  return {
    final: attributes['lk.transcription_final'] !== 'false',
    segmentId: attributes['lk.segment_id'] || info.id,
    speaker: speakerForTranscribedTrack(room, attributes['lk.transcribed_track_id']),
  };
}
