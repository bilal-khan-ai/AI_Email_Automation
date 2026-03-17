import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { 
  Layout, 
  Mail, 
  Ticket, 
  CheckCircle, 
  Clock, 
  Users, 
  Search,
  ChevronRight,
  RefreshCcw,
  LogOut
} from 'lucide-react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

import TicketDetail from './components/TicketDetail';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const fetchTickets = async () => {
  const { data } = await axios.get('/api/tickets');
  return data;
};

const Dashboard = () => {
  const [selectedTicket, setSelectedTicket] = useState(null);
  const { data, isLoading, refetch, isRefetching } = useQuery({
    queryKey: ['tickets'],
    queryFn: fetchTickets,
    refetchInterval: 5000, // Poll every 5 seconds
  });

  const stats = data?.stats || { total: 0, open: 0, closed: 0 };
  const tickets = data?.tickets || [];

  return (
    <div className="min-h-screen w-full flex flex-col p-4 md:p-8 bg-slate-950 text-slate-200">
      {/* Header */}
      <header className="flex justify-between items-center mb-8 glass p-6 rounded-2xl">
        <div>
          <h1 className="text-3xl font-bold gradient-text">Support AI</h1>
          <p className="text-slate-400 text-sm mt-1">Real-time automation dashboard</p>
        </div>
        <div className="flex items-center gap-4">
          <button 
            onClick={() => refetch()}
            className={cn(
              "p-2 rounded-lg hover:bg-slate-800 transition-colors",
              isRefetching && "animate-spin text-blue-400"
            )}
          >
            <RefreshCcw size={20} />
          </button>
          <div className="h-8 w-px bg-slate-800" />
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center font-bold">
              B
            </div>
            <button className="text-slate-400 hover:text-white transition-colors">
              <LogOut size={20} />
            </button>
          </div>
        </div>
      </header>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        <StatCard 
          icon={<Ticket className="text-blue-400" />} 
          label="Total Tickets" 
          value={stats.total} 
        />
        <StatCard 
          icon={<Clock className="text-amber-400" />} 
          label="Open Tickets" 
          value={stats.open} 
        />
        <StatCard 
          icon={<CheckCircle className="text-emerald-400" />} 
          label="Resolved" 
          value={stats.closed} 
        />
      </div>

      {/* Main Content */}
      <div className="flex-1 glass rounded-2xl overflow-hidden flex flex-col">
        <div className="p-6 border-b border-white/5 flex flex-col md:flex-row gap-4 justify-between items-center">
          <h2 className="text-xl font-semibold flex items-center gap-2">
            <Mail size={18} className="text-blue-400" />
            Recent Inbound
          </h2>
          <div className="relative w-full md:w-96">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={16} />
            <input 
              type="text" 
              placeholder="Search tickets or customers..."
              className="w-full bg-slate-900/50 border border-white/10 rounded-xl py-2 pl-10 pr-4 focus:ring-2 focus:ring-blue-500/50 outline-none transition-all"
            />
          </div>
        </div>

        <div className="flex-1 overflow-auto p-6">
          {isLoading ? (
            <div className="h-full flex items-center justify-center">
              <div className="animate-pulse flex flex-col items-center gap-4">
                <div className="h-12 w-12 rounded-full border-4 border-blue-500/20 border-t-blue-500 animate-spin" />
                <p className="text-slate-500">Retrieving intelligence...</p>
              </div>
            </div>
          ) : (
            <table className="w-full text-left">
              <thead>
                <tr className="text-slate-500 text-sm border-b border-white/5 pb-4">
                  <th className="pb-4 font-medium pl-2">Ticket</th>
                  <th className="pb-4 font-medium">Customer</th>
                  <th className="pb-4 font-medium">Subject</th>
                  <th className="pb-4 font-medium text-center">Status</th>
                  <th className="pb-4 font-medium text-right pr-2">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {tickets.map((t) => (
                  <tr 
                    key={t.ticket_id} 
                    className="group hover:bg-white/5 transition-colors cursor-pointer"
                    onClick={() => setSelectedTicket(t)}
                  >
                    <td className="py-4 pl-2">
                      <span className="font-mono text-xs text-blue-400 font-bold bg-blue-500/10 px-2 py-1 rounded">
                        {t.ticket_id}
                      </span>
                    </td>
                    <td className="py-4">
                      <div className="flex flex-col">
                        <span className="font-medium text-slate-200">
                          {t.customer_email?.split('@')[0]}
                        </span>
                        <span className="text-xs text-slate-500">{t.customer_email}</span>
                      </div>
                    </td>
                    <td className="py-4 max-w-xs truncate">
                      {t.subject}
                    </td>
                    <td className="py-4 text-center">
                      <span className={cn(
                        "text-[10px] font-bold uppercase tracking-wider px-2 py-1 rounded-full",
                        t.status === 'Open' ? "bg-amber-500/10 text-amber-400 border border-amber-500/20" : "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                      )}>
                        {t.status}
                      </span>
                    </td>
                    <td className="py-4 text-right pr-2">
                      <button className="p-2 rounded-lg hover:bg-blue-500/10 text-slate-400 hover:text-blue-400 transition-all">
                        <ChevronRight size={18} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {selectedTicket && (
        <TicketDetail 
          ticket={selectedTicket} 
          onClose={() => setSelectedTicket(null)} 
        />
      )}
    </div>
  );
};

const StatCard = ({ icon, label, value }) => (
  <div className="glass p-6 rounded-2xl flex items-center gap-4">
    <div className="p-4 bg-slate-900/50 rounded-xl">
      {icon}
    </div>
    <div>
      <p className="text-slate-500 text-xs font-medium uppercase tracking-wider">{label}</p>
      <p className="text-2xl font-bold mt-1">{value}</p>
    </div>
  </div>
);

export default Dashboard;
