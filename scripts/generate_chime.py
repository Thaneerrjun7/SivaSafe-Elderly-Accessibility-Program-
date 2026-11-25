import wave
import math
import struct
import os

def generate_chime(output_path):
    sample_rate = 44100
    duration_sec = 0.180  # 180ms
    num_samples = int(sample_rate * duration_sec)
    
    # Frequencies
    freq1 = 880.0
    freq2 = 1318.0
    
    # 90ms for each part roughly
    mid_sample = int(sample_rate * 0.09)
    
    # Attack in samples (5ms)
    attack_samples = int(sample_rate * 0.005)
    
    audio_data = bytearray()
    
    for i in range(num_samples):
        t = i / sample_rate
        
        # Determine frequency and crossfade
        if i < mid_sample:
            # First note
            freq = freq1
            # Simple envelope for the first note
            if i < attack_samples:
                env = i / attack_samples
            else:
                env = 1.0
        else:
            # Second note
            freq = freq2
            j = i - mid_sample
            if j < attack_samples:
                env = j / attack_samples
            else:
                # Exponential decay for the tail
                decay_pos = (j - attack_samples) / (num_samples - mid_sample - attack_samples)
                env = math.exp(-decay_pos * 4)  # decay factor
                
        # Sine wave
        val = math.sin(2.0 * math.pi * freq * t)
        
        # Apply envelope and volume
        vol = 0.6
        sample = int(val * env * vol * 32767.0)
        
        # Clip
        if sample > 32767: sample = 32767
        if sample < -32768: sample = -32768
        
        audio_data.extend(struct.pack('<h', sample))
        
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with wave.open(output_path, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio_data)
        
if __name__ == '__main__':
    generate_chime('assets/sounds/confirm_chime.wav')
    print('Generated chime.')
