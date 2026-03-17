import React from 'react';
import { X, User, Tag, Send, Paperclip, MessageSquare } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';

const fetchMessages = async (ticketId) => {
  const { data } = await axios.get(`/api/ticket_messages/${ticketId}`);
  return data.messages || [];
};

const TicketDetail = ({ ticket, onClose }) => {
  const { data: messages, isLoading } = useQuery({
    queryKey: ['messages', ticket.ticket_id],
    queryFn: () => fetchMessages(ticket.ticket_id),
    enabled: !!ticket.ticket_id,
  });

  if (!ticket) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm">
      <div className="glass w-full max-w-5xl h-[85vh] rounded-3xl flex flex-col overflow-hidden shadow-2xl animate-in fade-in zoom-in duration-200">
        {/* Header */}
        <div className="p-6 border-b border-white/5 flex justify-between items-start">
          <div className="flex gap-4">
            <div className="w-12 h-12 rounded-2xl bg-blue-500/20 flex items-center justify-center text-blue-400">
              <Ticket size={24} />
            </div>
            <div>
              <div className="flex items-center gap-2 mb-1">
                <span className="text-xs font-mono font-bold text-blue-400 bg-blue-500/10 px-2 py-0.5 rounded">
                  {ticket.ticket_id}
                </span>
                <span className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full border ${
                  ticket.status === 'Open' ? "bg-amber-500/10 text-amber-400 border-amber-500/20" : "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                }`}>
                  {ticket.status}
                </span>
              </div>
              <h2 className="text-xl font-semibold text-slate-100">{ticket.subject}</h2>
            </div>
          </div>
          <button 
            onClick={onClose}
            className="p-2 hover:bg-white/5 rounded-xl text-slate-500 hover:text-white transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        <div className="flex-1 flex overflow-hidden">
          {/* Main Chat/Thread */}
          <div className="flex-1 flex flex-col border-r border-white/5 bg-slate-900/40">
            <div className="flex-1 overflow-y-auto p-6 space-y-6">
              {isLoading ? (
                <div className="flex justify-center py-10">
                  <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
                </div>
              ) : (
                messages.map((msg, idx) => (
                  <MessageBubble key={idx} message={msg} />
                ))
              )}
            </div>

            {/* AI Draft Area */}
            <div className="p-6 bg-slate-900/60 border-t border-white/5">
              <div className="flex items-center justify-between mb-3">
                <label className="text-xs font-bold text-blue-400 uppercase tracking-widest flex items-center gap-2">
                  <Tag size={14} />
                  AI Suggested Response
                </label>
                <button className="text-[10px] text-slate-500 hover:text-slate-300 underline font-medium">
                  Regenerate
                </button>
              </div>
              <textarea 
                className="w-full h-32 bg-slate-950/50 border border-blue-500/20 rounded-2xl p-4 text-slate-200 text-sm focus:ring-2 focus:ring-blue-500/30 outline-none transition-all resize-none shadow-inner"
                defaultValue={ticket.ai_draft || ""}
                placeholder="Drafting intelligent response..."
              />
              <div className="flex justify-between mt-4">
                <button className="flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-sm font-medium transition-all">
                  <Paperclip size={16} />
                  Attach Files
                </button>
                <button className="flex items-center gap-2 px-6 py-2 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-sm font-bold transition-all shadow-lg shadow-blue-600/20 group">
                  Send Response
                  <Send size={16} className="group-hover:translate-x-1 group-hover:-translate-y-1 transition-transform" />
                </button>
              </div>
            </div>
          </div>

          {/* Sidebar Area */}
          <div className="w-80 p-6 space-y-8 bg-slate-900/20">
            <section>
              <h3 className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-4">Customer Info</h3>
              <div className="flex items-center gap-3 p-3 rounded-2xl bg-white/5 border border-white/5">
                <div className="w-10 h-10 rounded-xl bg-slate-800 flex items-center justify-center text-slate-400">
                  <User size={20} />
                </div>
                <div className="overflow-hidden">
                  <p className="font-medium text-slate-200 truncate">{ticket.customer_email?.split('@')[0]}</p>
                  <p className="text-[10px] text-slate-500 truncate">{ticket.customer_email}</p>
                </div>
              </div>
            </section>

            <section>
              <h3 className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-4">Metadata</h3>
              <div className="space-y-3">
                <MetaItem label="Created" value={new Date(ticket.created_at).toLocaleDateString()} />
                <MetaItem label="Assigned To" value={ticket.assigned_to || "Unassigned"} icon={<Users size={12}/>} />
                <MetaItem label="AI Confidence" value="High (92%)" />
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
};

const MessageBubble = ({ message }) => {
  const isAgent = message.is_internal;
  return (
    <div className={`flex ${isAgent ? 'justify-end' : 'justify-start'}`}>
      <div className={`max-w-[85%] p-4 rounded-2xl border ${
        isAgent 
          ? 'bg-blue-600/10 border-blue-500/20 text-slate-200' 
          : 'bg-slate-800/40 border-white/5 text-slate-300'
      }`}>
        <div className="flex items-center gap-2 mb-2 text-[10px] font-bold uppercase tracking-wider">
          {isAgent ? <Users size={10} className="text-blue-400"/> : <User size={10} className="text-slate-500"/>}
          <span className={isAgent ? 'text-blue-400' : 'text-slate-500'}>
            {isAgent ? 'AI Assistant' : 'Customer'}
          </span>
          <span className="text-slate-600 font-normal ml-auto">
            {new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </span>
        </div>
        <p className="text-sm leading-relaxed whitespace-pre-wrap">{message.body_text}</p>
      </div>
    </div>
  );
};

const MetaItem = ({ label, value, icon }) => (
  <div className="flex justify-between items-center text-xs">
    <span className="text-slate-500">{label}</span>
    <span className="font-medium text-slate-300 flex items-center gap-1">
      {icon}
      {value}
    </span>
  </div>
);

export default TicketDetail;
