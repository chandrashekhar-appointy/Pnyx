'use client';

import React, { useState, useEffect } from 'react';
import { X, CheckCircle2, AlertCircle, Loader2, QrCode } from 'lucide-react';
import { authFetch } from '@/lib/api';
import { 
  Dialog, 
  DialogContent, 
  DialogHeader, 
  DialogTitle, 
  DialogDescription,
  DialogFooter
} from '@/components/ui/dialog';
import { toast } from 'sonner';
import Analytics from '@/lib/analytics';

interface PurchaseCreditsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

const CREDIT_PACKS = [
  { amount: 99, credits: 5000, label: 'Starter' },
  { amount: 199, credits: 12000, label: 'Pro', popular: true },
  { amount: 499, credits: 35000, label: 'Business' },
  { amount: 999, credits: 80000, label: 'Enterprise' },
];

export const PurchaseCreditsModal: React.FC<PurchaseCreditsModalProps> = ({ isOpen, onClose }) => {
  const [selectedPack, setSelectedPack] = useState<typeof CREDIT_PACKS[0] | null>(null);
  const [loading, setLoading] = useState(false);
  const [purchaseData, setPurchaseData] = useState<{
    qr_code_url: string;
    purchase_id: string;
    credits_to_add: number;
  } | null>(null);
  const [status, setStatus] = useState<'selecting' | 'paying' | 'success' | 'error'>('selecting');

  const handlePurchase = async (pack: typeof CREDIT_PACKS[0]) => {
    setSelectedPack(pack);
    setLoading(true);
    Analytics.trackCreditPackSelected({ pack_label: pack.label, amount_inr: pack.amount, credits: pack.credits });
    try {
      const response = await authFetch('/api/credits/purchase', {
        method: 'POST',
        body: JSON.stringify({ amount_inr: pack.amount }),
      });

      if (!response.ok) {
        throw new Error('Failed to initiate purchase');
      }

      const data = await response.json();
      setPurchaseData(data);
      setStatus('paying');
      Analytics.trackPaymentInitiated({ purchase_id: data.purchase_id, amount_inr: pack.amount, credits: pack.credits });
    } catch (error) {
      console.error('Purchase error:', error);
      toast.error('Could not create payment QR. Please try again.');
      setStatus('selecting');
    } finally {
      setLoading(false);
    }
  };

  // Polling for status
  useEffect(() => {
    let intervalId: NodeJS.Timeout;

    if (status === 'paying' && purchaseData?.purchase_id) {
      intervalId = setInterval(async () => {
        try {
          const response = await authFetch(`/api/credits/purchase/${purchaseData.purchase_id}`);
          if (response.ok) {
            const { status: currentStatus } = await response.json();
            if (currentStatus === 'completed') {
              setStatus('success');
              Analytics.trackPaymentCompleted({ purchase_id: purchaseData.purchase_id, amount_inr: selectedPack?.amount, credits: selectedPack?.credits });
              toast.success('Credits added successfully!');
              // Dispatch event to refresh balance
              window.dispatchEvent(new CustomEvent('payment:success'));
              clearInterval(intervalId);
            } else if (currentStatus === 'failed') {
              setStatus('error');
              Analytics.trackPaymentFailed({ purchase_id: purchaseData.purchase_id, amount_inr: selectedPack?.amount });
              clearInterval(intervalId);
            }
          }
        } catch (error) {
          console.error('Polling error:', error);
        }
      }, 3000); // Poll every 3 seconds
    }

    return () => {
      if (intervalId) clearInterval(intervalId);
    };
  }, [status, purchaseData]);

  const reset = () => {
    setStatus('selecting');
    setSelectedPack(null);
    setPurchaseData(null);
  };

  const handleClose = () => {
    if (status === 'success') {
      reset();
    }
    onClose();
  };

  return (
    <Dialog open={isOpen} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle className="text-xl font-bold">
            {status === 'selecting' && 'Get More Credits'}
            {status === 'paying' && 'Scan to Pay'}
            {status === 'success' && 'Payment Successful'}
            {status === 'error' && 'Payment Failed'}
          </DialogTitle>
          <DialogDescription>
            {status === 'selecting' && 'Choose a credit pack to continue your meetings.'}
            {status === 'paying' && `Pay ₹${selectedPack?.amount} using any UPI app to get ${selectedPack?.credits.toLocaleString()} credits.`}
            {status === 'success' && 'Your credits have been added to your account.'}
          </DialogDescription>
        </DialogHeader>

        <div className="py-4">
          {status === 'selecting' && (
            <div className="grid grid-cols-2 gap-3">
              {CREDIT_PACKS.map((pack) => (
                <button
                  key={pack.amount}
                  onClick={() => handlePurchase(pack)}
                  disabled={loading && selectedPack?.amount === pack.amount}
                  className={`relative p-4 rounded-xl border-2 text-left transition-all ${
                    selectedPack?.amount === pack.amount
                      ? 'border-blue-600 bg-blue-50'
                      : 'border-gray-100 hover:border-blue-200 bg-white'
                  }`}
                >
                  {pack.popular && (
                    <span className="absolute -top-2 -right-2 bg-blue-600 text-white text-[10px] px-2 py-0.5 rounded-full font-bold">
                      POPULAR
                    </span>
                  )}
                  <div className="text-sm font-bold text-gray-900">{pack.label}</div>
                  <div className="text-xl font-black text-blue-600">
                    {pack.credits.toLocaleString()}
                  </div>
                  <div className="text-xs text-gray-500 mt-1">credits for ₹{pack.amount}</div>
                  
                  {loading && selectedPack?.amount === pack.amount && (
                    <div className="absolute inset-0 bg-white/50 flex items-center justify-center rounded-xl">
                      <Loader2 className="animate-spin text-blue-600" size={24} />
                    </div>
                  )}
                </button>
              ))}
            </div>
          )}

          {status === 'paying' && purchaseData && (
            <div className="flex flex-col items-center">
              <div className="bg-white p-4 rounded-xl border-2 border-gray-100 shadow-sm mb-4">
                <img 
                  src={purchaseData.qr_code_url} 
                  alt="UPI QR Code" 
                  className="w-48 h-48 object-contain"
                />
              </div>
              <div className="flex items-center gap-2 text-sm text-gray-600 animate-pulse">
                <Loader2 size={16} className="animate-spin" />
                <span>Waiting for payment confirmation...</span>
              </div>
              <button 
                onClick={reset}
                className="mt-6 text-sm text-gray-500 hover:text-gray-700 underline"
              >
                Choose different pack
              </button>
            </div>
          )}

          {status === 'success' && (
            <div className="flex flex-col items-center py-6 text-center">
              <div className="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mb-4">
                <CheckCircle2 className="text-green-600" size={32} />
              </div>
              <h3 className="text-lg font-bold text-gray-900">Purchase Confirmed!</h3>
              <p className="text-gray-600 mt-1">
                {selectedPack?.credits.toLocaleString()} credits have been added.
              </p>
            </div>
          )}

          {status === 'error' && (
            <div className="flex flex-col items-center py-6 text-center">
              <div className="w-16 h-16 bg-red-100 rounded-full flex items-center justify-center mb-4">
                <AlertCircle className="text-red-600" size={32} />
              </div>
              <h3 className="text-lg font-bold text-gray-900">Payment Unsuccessful</h3>
              <p className="text-gray-600 mt-1">
                Something went wrong with the payment transaction.
              </p>
              <button 
                onClick={reset}
                className="mt-4 px-4 py-2 bg-gray-900 text-white rounded-lg text-sm font-medium"
              >
                Try Again
              </button>
            </div>
          )}
        </div>

        <DialogFooter className="sm:justify-center border-t pt-4">
          <div className="flex items-center gap-2 text-[10px] text-gray-400 uppercase tracking-widest font-bold">
            <QrCode size={12} />
            <span>Secure UPI Payment</span>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
