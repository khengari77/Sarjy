import { Room, RoomEvent, Track } from 'livekit-client';
import type { SessionConfig } from '../api';
export type VoiceEvent = {type:'status'|'transcript'|'error'|'audio-blocked';text:string};
export interface VoiceRuntime {
 connect(config:SessionConfig):Promise<void>;
 startMicrophone():Promise<void>;
 stopMicrophone():Promise<void>;
 interrupt():Promise<void>;
 sendText(text:string):Promise<void>;
 disconnect():Promise<void>;
 subscribe(handler:(event:VoiceEvent)=>void):()=>void;
}
export class LiveKitVoiceRuntime implements VoiceRuntime {
 private room:Room|null=null;
 private audio = new Set<HTMLMediaElement>();
 private handlers = new Set<(event:VoiceEvent)=>void>();
 subscribe(handler:(event:VoiceEvent)=>void){this.handlers.add(handler);return ()=>{this.handlers.delete(handler)}}
 private emit(type:VoiceEvent['type'],text:string){for(const handler of this.handlers)handler({type,text})}
 async connect(config:SessionConfig){
  await this.disconnect();
  const room = new Room(); this.room = room;
  room.on(RoomEvent.TrackSubscribed,(track)=>{if(track.kind===Track.Kind.Audio){const element=track.attach();this.audio.add(element);document.body.appendChild(element)}});
  room.on(RoomEvent.TrackUnsubscribed,(track)=>{for(const element of track.detach()){element.remove();this.audio.delete(element)}});
  room.on(RoomEvent.AudioPlaybackStatusChanged,()=>{if(!room.canPlaybackAudio)this.emit('audio-blocked','AUDIO_PLAYBACK_BLOCKED')});
  room.on(RoomEvent.Reconnecting,()=>this.emit('status','reconnecting'));
  room.on(RoomEvent.Reconnected,()=>this.emit('status','connected'));
  room.on(RoomEvent.Disconnected,()=>this.emit('status','disconnected'));
  room.on(RoomEvent.ParticipantAttributesChanged,(attributes)=>{const state=attributes['lk.agent.state'];if(state)this.emit('status',state)});
  room.on(RoomEvent.DataReceived,(payload,participant,kind,topic)=>{if(topic==='sarjy.error' && participant?.isAgent){try{const event=JSON.parse(new TextDecoder().decode(payload));if(['DAILY_BUDGET_EXHAUSTED','BUDGET_SERVICE_UNAVAILABLE'].includes(event.code))this.emit('error',event.code)}catch{}}});
  room.on(RoomEvent.MediaDevicesError,()=>this.emit('error','MICROPHONE_UNAVAILABLE'));
  room.registerTextStreamHandler('lk.transcription',async(reader)=>{try{const text=await reader.readAll();if(this.room===room)this.emit('transcript',text)}catch{if(this.room===room)this.emit('error','TRANSCRIPT_UNAVAILABLE')}});
  this.emit('status','connecting');
  await room.connect(config.url,config.token);
  await room.startAudio();
  this.emit('status','starting');
  await this.waitForAgent(room);
 }
 private async waitForAgent(room:Room){
  const deadline=Date.now()+45000;
  while(this.room===room && Date.now()<deadline){
   const agent=[...room.remoteParticipants.values()].find(p=>p.isAgent);
   if(agent && ['listening','thinking','speaking'].includes(agent.attributes['lk.agent.state'])){
    this.emit('status',agent.attributes['lk.agent.state']);return;
   }
   await new Promise(resolve=>setTimeout(resolve,100));
  }
  throw new Error('AGENT_START_TIMEOUT');
 }
 async resumeAudio(){await this.room?.startAudio()}
 microphoneStream():MediaStream|null{const track=this.room?.localParticipant.getTrackPublication(Track.Source.Microphone)?.track?.mediaStreamTrack;return track?new MediaStream([track]):null}
 async startMicrophone(){await this.room?.localParticipant.setMicrophoneEnabled(true)}
 async stopMicrophone(){await this.room?.localParticipant.setMicrophoneEnabled(false)}
 async sendText(text:string){if(!this.room)throw new Error('CALL_REQUIRED');await this.room.localParticipant.sendText(text,{topic:'lk.chat'})}
 async interrupt(){
  const agent=[...(this.room?.remoteParticipants.values() || [])].find(p=>p.isAgent);
  if(agent && this.room)await this.room.localParticipant.performRpc({destinationIdentity:agent.identity,method:'sarjy.interrupt',payload:''});
 }
 async disconnect(){const room=this.room;this.room=null;if(room){await room.disconnect();room.removeAllListeners()}for(const element of this.audio){element.pause();element.srcObject=null;element.remove()}this.audio.clear()}
}
