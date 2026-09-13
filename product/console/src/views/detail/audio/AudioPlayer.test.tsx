import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeAll } from 'vitest';
import AudioPlayer from './AudioPlayer';

beforeAll(() => {
  // jsdom implements neither media playback nor object URLs.
  (globalThis.URL as any).createObjectURL = vi.fn(() => 'blob:mock');
  (globalThis.URL as any).revokeObjectURL = vi.fn();
  Object.defineProperty(HTMLMediaElement.prototype, 'play', {
    configurable: true,
    value: vi.fn().mockResolvedValue(undefined),
  });
  Object.defineProperty(HTMLMediaElement.prototype, 'pause', { configurable: true, value: vi.fn() });
});

describe('AudioPlayer', () => {
  it('shows the explicit "not available" state, with the toggle disabled, when there is no audio', () => {
    render(<AudioPlayer file={null} durationS={42} />);
    expect(screen.getByTestId('audio-player').dataset.state).toBe('unavailable');
    expect(screen.getByTestId('audio-unavailable').textContent).toMatch(/not available/i);
    expect((screen.getByTestId('audio-toggle') as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByTestId('audio-timeline')).toBeNull();
  });

  it('renders controls for a real clip and syncs the position via onTime', () => {
    const onTime = vi.fn();
    const file = new File([new Uint8Array(64)], 'clip.wav', { type: 'audio/wav' });
    render(<AudioPlayer file={file} durationS={12} onTime={onTime} />);

    const player = screen.getByTestId('audio-player');
    expect(player.dataset.state).toBe('loading');

    // Metadata arrives -> ready, duration from the element wins over the fallback.
    const audio = player.querySelector('audio') as HTMLAudioElement;
    Object.defineProperty(audio, 'duration', { configurable: true, value: 30 });
    fireEvent.loadedMetadata(audio);
    expect(player.dataset.state).toBe('ready');
    expect((screen.getByTestId('audio-toggle') as HTMLButtonElement).disabled).toBe(false);

    // Seeking the timeline pushes the position up (this is what moves the trace playhead).
    fireEvent.change(screen.getByTestId('audio-timeline'), { target: { value: '7.5' } });
    expect(onTime).toHaveBeenLastCalledWith(7.5);
    expect(screen.getByTestId('audio-current').textContent).toBe('0:07');

    // Timeupdate from the element does the same while playing.
    Object.defineProperty(audio, 'currentTime', { configurable: true, value: 9, writable: true });
    fireEvent.timeUpdate(audio);
    expect(onTime).toHaveBeenLastCalledWith(9);
  });

  it('degrades to an explicit error state when the browser cannot decode the clip', () => {
    const file = new File([new Uint8Array(8)], 'broken.wav', { type: 'audio/wav' });
    render(<AudioPlayer file={file} />);
    const audio = screen.getByTestId('audio-player').querySelector('audio') as HTMLAudioElement;
    fireEvent.error(audio);
    expect(screen.getByTestId('audio-player').dataset.state).toBe('error');
    expect(screen.getByTestId('audio-unavailable').textContent).toMatch(/could not be decoded/i);
  });
});
