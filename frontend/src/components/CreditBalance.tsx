'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { useSession } from 'next-auth/react';
import { Coins, Loader2, Infinity } from 'lucide-react';
import { authFetch } from '@/lib/api';

interface CreditBalanceData {
  weekly: number;
  admin: number;
  purchased: number;
  total: number;
  is_unlimited: boolean;
}

interface CreditBalanceProps {
  className?: string;
}

export const CreditBalance: React.FC<CreditBalanceProps> = ({ className = '' }) => {
  const { status } = useSession();
  const [balance, setBalance] = useState<CreditBalanceData | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchBalance = useCallback(async () => {
    if (status !== 'authenticated') return;
    
    setLoading(true);
    try {
      const response = await authFetch('/api/credits');
      if (response.ok) {
        const data = await response.json();
        setBalance(data);
      }
    } catch (error) {
      console.error('Failed to fetch credit balance:', error);
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    fetchBalance();
    
    // Refresh balance when a payment is successful (via custom event)
    const handlePaymentSuccess = () => {
      fetchBalance();
    };

    window.addEventListener('payment:success', handlePaymentSuccess);
    return () => window.removeEventListener('payment:success', handlePaymentSuccess);
  }, [fetchBalance]);

  if (status !== 'authenticated') return null;

  return (
    <div className={`p-3 bg-gray-50 rounded-xl border border-gray-100 ${className}`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-gray-500 uppercase tracking-wider">
          <Coins size={14} className="text-amber-500" />
          <span>Credits</span>
        </div>
        {loading ? (
          <Loader2 size={12} className="animate-spin text-gray-400" />
        ) : (
          <button 
            onClick={fetchBalance}
            className="text-[10px] text-blue-600 hover:underline"
          >
            Refresh
          </button>
        )}
      </div>

      <div className="flex items-end justify-between">
        <div>
          {balance?.is_unlimited ? (
            <div className="flex items-center gap-1 text-2xl font-bold text-gray-900">
              <Infinity className="text-blue-600" size={24} />
              <span className="text-sm font-medium text-gray-500 ml-1 italic">Unlimited</span>
            </div>
          ) : (
            <div className="flex flex-col">
              <span className="text-2xl font-bold text-gray-900">
                {balance?.total !== undefined ? balance.total.toLocaleString() : '---'}
              </span>
              <span className="text-[10px] text-gray-500">
                {balance ? (
                  <>
                    {balance.weekly > 0 && `${balance.weekly.toLocaleString()} weekly • `}
                    {(balance.purchased > 0 || balance.admin > 0) && `${(balance.purchased + balance.admin).toLocaleString()} bonus`}
                  </>
                ) : 'Loading balance...'}
              </span>
            </div>
          )}
        </div>
        
      </div>
    </div>
  );
};
