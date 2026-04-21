'use client';

import React, { useState, useEffect } from 'react';
import { useSession } from 'next-auth/react';
import { authFetch } from '@/lib/api';
import { toast } from 'sonner';
import { Loader2, Send, CheckCircle, AlertCircle, Clock, XCircle, RotateCcw } from 'lucide-react';
import Analytics from '@/lib/analytics';

interface FeedbackItem {
  id: string;
  user_email: string;
  type: 'bug' | 'feature' | 'general';
  title: string;
  description: string;
  status: 'pending' | 'in_review' | 'in_progress' | 'completed' | 'rejected';
  created_at: string;
  updated_at: string;
}

export default function FeedbackPage() {
  const { data: session } = useSession();
  const [activeTab, setActiveTab] = useState<'submit' | 'history'>('submit');
  const [isAdmin, setIsAdmin] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [feedbackList, setFeedbackList] = useState<FeedbackItem[]>([]);
  
  // Form State
  const [formData, setFormData] = useState({
    type: 'general',
    title: '',
    description: ''
  });
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    Analytics.trackPageView('feedback');
    checkAdminStatus();
    fetchFeedback();
  }, []);

  const checkAdminStatus = async () => {
    try {
      const res = await authFetch('/feedback/check-admin');
      if (res.ok) {
        const data = await res.json();
        setIsAdmin(data.is_admin);
        if (data.is_admin) setActiveTab('history'); // Admins likely want to see history first
      }
    } catch (e) {
      console.error(e);
    }
  };

  const fetchFeedback = async () => {
    setIsLoading(true);
    try {
      const res = await authFetch('/feedback/');
      if (res.ok) {
        const data = await res.json();
        setFeedbackList(data);
      }
    } catch (e) {
      toast.error('Failed to load feedback');
    } finally {
      setIsLoading(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.title.trim()) return toast.error('Title is required');
    
    setIsSubmitting(true);
    try {
      const res = await authFetch('/feedback/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData)
      });

      if (res.ok) {
        await Analytics.trackFeedbackSubmitted({
          type: formData.type,
          title_length: formData.title.trim().length,
          description_length: formData.description.trim().length,
        });
        toast.success('Feedback submitted successfully!');
        setFormData({ type: 'general', title: '', description: '' });
        fetchFeedback(); // Refresh list
        setActiveTab('history');
      } else {
        throw new Error('Failed to submit');
      }
    } catch (e) {
      toast.error('Error submitting feedback');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleStatusUpdate = async (id: string, newStatus: string) => {
    try {
      const res = await authFetch(`/feedback/${id}/status`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus })
      });

      if (res.ok) {
        await Analytics.trackFeedbackStatusUpdated({
          feedback_id: id,
          new_status: newStatus,
        });
        toast.success('Status updated');
        setFeedbackList(prev => prev.map(item => 
          item.id === id ? { ...item, status: newStatus as any } : item
        ));
      }
    } catch (e) {
      toast.error('Failed to update status');
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'completed': return 'bg-green-100 text-green-800 border-green-200';
      case 'in_progress': return 'bg-blue-100 text-blue-800 border-blue-200';
      case 'rejected': return 'bg-red-100 text-red-800 border-red-200';
      case 'in_review': return 'bg-yellow-100 text-yellow-800 border-yellow-200';
      default: return 'bg-gray-100 text-gray-800 border-gray-200';
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'completed': return <CheckCircle className="w-4 h-4" />;
      case 'in_progress': return <RotateCcw className="w-4 h-4" />;
      case 'rejected': return <XCircle className="w-4 h-4" />;
      default: return <Clock className="w-4 h-4" />;
    }
  };

  return (
    <div className="flex flex-col h-screen bg-gray-50 overflow-hidden">
      <div className="flex-1 overflow-y-auto p-8">
        <div className="max-w-4xl mx-auto">
          <h1 className="text-3xl font-bold text-gray-900 mb-2">Feedback & Support</h1>
          <p className="text-gray-600 mb-8">Help us improve by reporting bugs or requesting features.</p>

          {/* Tabs */}
          <div className="flex space-x-4 mb-6 border-b border-gray-200">
            <button
              onClick={() => setActiveTab('submit')}
              className={`pb-3 px-1 font-medium text-sm transition-colors relative ${
                activeTab === 'submit' ? 'text-blue-600 border-b-2 border-blue-600' : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              Submit New
            </button>
            <button
              onClick={() => setActiveTab('history')}
              className={`pb-3 px-1 font-medium text-sm transition-colors relative ${
                activeTab === 'history' ? 'text-blue-600 border-b-2 border-blue-600' : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              {isAdmin ? 'All Feedback (Admin)' : 'My History'}
            </button>
          </div>

          {activeTab === 'submit' ? (
            <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
              <form onSubmit={handleSubmit} className="space-y-6">
                <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
                  <div className="md:col-span-1">
                    <label className="block text-sm font-medium text-gray-700 mb-2">Type</label>
                    <div className="space-y-2">
                      {['general', 'bug', 'feature'].map((t) => (
                        <label key={t} className={`flex items-center p-3 rounded-lg border cursor-pointer transition-all ${
                          formData.type === t 
                            ? 'bg-blue-50 border-blue-200 ring-1 ring-blue-500' 
                            : 'bg-white border-gray-200 hover:bg-gray-50'
                        }`}>
                          <input
                            type="radio"
                            name="type"
                            value={t}
                            checked={formData.type === t}
                            onChange={(e) => setFormData({...formData, type: e.target.value})}
                            className="text-blue-600 focus:ring-blue-500"
                          />
                          <span className="ml-3 capitalize font-medium text-gray-700">{t}</span>
                        </label>
                      ))}
                    </div>
                  </div>

                  <div className="md:col-span-3 space-y-6">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-2">Title</label>
                      <input
                        type="text"
                        value={formData.title}
                        onChange={(e) => setFormData({...formData, title: e.target.value})}
                        className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-all"
                        placeholder={formData.type === 'bug' ? "e.g., Audio stopped working during call" : "e.g., Add dark mode support"}
                        required
                        minLength={5}
                      />
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-2">Description</label>
                      <textarea
                        value={formData.description}
                        onChange={(e) => setFormData({...formData, description: e.target.value})}
                        className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-all min-h-[150px]"
                        placeholder="Provide as much detail as possible..."
                      />
                    </div>

                    <div className="flex justify-end pt-4">
                      <button
                        type="submit"
                        disabled={isSubmitting}
                        className="flex items-center px-6 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium shadow-sm transition-all disabled:opacity-70 disabled:cursor-not-allowed"
                      >
                        {isSubmitting ? (
                          <>
                            <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                            Submitting...
                          </>
                        ) : (
                          <>
                            <Send className="w-4 h-4 mr-2" />
                            Submit Feedback
                          </>
                        )}
                      </button>
                    </div>
                  </div>
                </div>
              </form>
            </div>
          ) : (
            <div className="space-y-4">
              {isLoading ? (
                <div className="flex justify-center py-12">
                  <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
                </div>
              ) : feedbackList.length === 0 ? (
                <div className="text-center py-12 bg-white rounded-xl border border-gray-200">
                  <div className="bg-gray-50 w-16 h-16 rounded-full flex items-center justify-center mx-auto mb-4">
                    <MessageSquare className="w-8 h-8 text-gray-400" />
                  </div>
                  <h3 className="text-lg font-medium text-gray-900">No feedback yet</h3>
                  <p className="text-gray-500 mt-1">Submit your first feedback item to get started.</p>
                </div>
              ) : (
                feedbackList.map((item) => (
                  <div key={item.id} className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 transition-all hover:shadow-md">
                    <div className="flex items-start justify-between">
                      <div className="space-y-1">
                        <div className="flex items-center gap-3 mb-2">
                          <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium uppercase tracking-wide ${
                            item.type === 'bug' ? 'bg-red-100 text-red-800' : 
                            item.type === 'feature' ? 'bg-purple-100 text-purple-800' : 
                            'bg-gray-100 text-gray-800'
                          }`}>
                            {item.type}
                          </span>
                          <span className="text-xs text-gray-500">
                            {new Date(item.created_at).toLocaleDateString()}
                          </span>
                          {isAdmin && (
                            <span className="text-xs text-gray-400 font-mono">
                              {item.user_email}
                            </span>
                          )}
                        </div>
                        <h3 className="text-lg font-semibold text-gray-900">{item.title}</h3>
                        <p className="text-gray-600 whitespace-pre-wrap">{item.description}</p>
                      </div>

                      <div className="flex flex-col items-end gap-2">
                        {isAdmin ? (
                          <select
                            value={item.status}
                            onChange={(e) => handleStatusUpdate(item.id, e.target.value)}
                            className={`text-xs font-medium px-3 py-1.5 rounded-full border cursor-pointer outline-none focus:ring-2 focus:ring-offset-1 focus:ring-blue-500 ${getStatusColor(item.status)}`}
                          >
                            <option value="pending">Pending</option>
                            <option value="in_review">In Review</option>
                            <option value="in_progress">In Progress</option>
                            <option value="completed">Completed</option>
                            <option value="rejected">Rejected</option>
                          </select>
                        ) : (
                          <div className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border ${getStatusColor(item.status)}`}>
                            {getStatusIcon(item.status)}
                            <span className="capitalize">{item.status.replace('_', ' ')}</span>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Helper for icon since we didn't import MessageSquare in the component body
function MessageSquare({ className }: { className?: string }) {
  return (
    <svg 
      xmlns="http://www.w3.org/2000/svg" 
      width="24" 
      height="24" 
      viewBox="0 0 24 24" 
      fill="none" 
      stroke="currentColor" 
      strokeWidth="2" 
      strokeLinecap="round" 
      strokeLinejoin="round" 
      className={className}
    >
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  );
}
