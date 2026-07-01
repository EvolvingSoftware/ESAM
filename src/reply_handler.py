import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

class ReplyHandler:
    def __init__(self):
        self.patterns = {
            'dispute': {
                'keywords': ['dispute', 'incorrect', 'wrong', 'already paid', 'didn\'t receive',
                             'returned', 'not mine', 'error', 'mistake', 'refuse', 'challenge'],
                'reason_patterns': [
                    r'because\s+(.+?)(?:\.|$)',
                    r'reason:\s*(.+?)(?:\.|$)',
                    r'I\s+dispute\s+(.+?)(?:\.|$)',
                    r'already\s+paid\s+(.+?)(?:\.|$)',
                    r'didn\'t\s+receive\s+(.+?)(?:\.|$)',
                    r'returned\s+(.+?)(?:\.|$)'
                ]
            },
            'promise_to_pay': {
                'keywords': ['will pay', 'promise to pay', 'payment plan', 'pay by', 'schedule payment',
                             'arrange payment', 'set up payment', 'pay on', 'pay next'],
                'date_patterns': [
                    r'(?:will|going to|plan to|promise to)\s+pay\s+(?:on|by|before)?\s*(.+?)(?:\.|,|$)',
                    r'pay\s+(?:on|by|before)\s+(.+?)(?:\.|,|$)',
                    r'by\s+(friday|monday|tuesday|wednesday|thursday|saturday|sunday)',
                    r'next\s+(week|month|payday|friday|monday)',
                    r'(?:in|within)\s+(\d+)\s+(?:days|business days|working days)',
                    r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                    r'(\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*(?:\s+\d{2,4})?)'
                ]
            },
            'out_of_office': {
                'keywords': ['out of office', 'ooo', 'away', 'vacation', 'holiday', 'not available',
                             'automated reply', 'auto-reply', 'will return', 'back on', 'back in'],
                'return_patterns': [
                    r'return(?:ing)?\s+(?:on|in|around)\s+(.+?)(?:\.|,|$)',
                    r'back\s+(?:on|in|around)\s+(.+?)(?:\.|,|$)',
                    r'(?:will be|am)\s+(?:back|returning)\s+(.+?)(?:\.|,|$)',
                    r'(?:from|until)\s+(.+?)(?:\.|,|$)',
                    r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                    r'(\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*(?:\s+\d{2,4})?)'
                ]
            },
            'query': {
                'keywords': ['question', 'query', 'inquiry', 'ask', 'need information', 'details',
                             'explain', 'what is', 'how much', 'balance', 'statement', 'account'],
                'query_phrases': [
                    r'(?:can|could|would)\s+you\s+(.+?)(?:\?|$)',
                    r'(?:please|kindly)\s+(?:send|provide|give)\s+(.+?)(?:\.|$)',
                    r'(?:what|how|why|when)\s+(.+?)(?:\?|$)'
                ]
            },
            'payment_confirmation': {
                'keywords': ['payment made', 'paid', 'receipt', 'transaction', 'confirm payment',
                             'payment confirmation', 'reference', 'confirmation number', 'paid in full'],
                'reference_patterns': [
                    r'reference(?:\s*number)?:?\s*(.+?)(?:\.|,|$)',
                    r'confirmation(?:\s*number)?:?\s*(.+?)(?:\.|,|$)',
                    r'transaction(?:\s*id)?:?\s*(.+?)(?:\.|,|$)',
                    r'receipt(?:\s*number)?:?\s*(.+?)(?:\.|,|$)',
                    r'(?:paid|payment)\s+(?:of\s+)?\$?([\d,]+(?:\.\d{2})?)\s+(?:on|for|via)\s+(.+?)(?:\.|,|$)'
                ]
            },
            'unsubscribe': {
                'keywords': ['unsubscribe', 'remove me', 'opt out', 'no longer', 'do not contact',
                             'stop emailing', 'cease communication', 'remove from list'],
                'unsubscribe_patterns': [
                    r'(?:please\s+)?(?:unsubscribe|remove\s+me|opt\s+out)\s*(?:from|of)\s*(.+?)(?:\.|$)',
                    r'(?:do\s+not|don\'t)\s+(?:contact|email|call)\s+(?:me\s+)?(?:about|regarding|for)\s+(.+?)(?:\.|$)'
                ]
            }
        }
        
        self.day_names = {
            'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
            'friday': 4, 'saturday': 5, 'sunday': 6
        }
        
        self.month_names = {
            'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
            'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6,
            'jul': 7, 'july': 7, 'aug': 8, 'august': 8, 'sep': 9, 'september': 9,
            'oct': 10, 'october': 10, 'nov': 11, 'november': 11, 'dec': 12, 'december': 12
        }

    def _parse_date(self, date_str: str, reference_date: Optional[datetime] = None) -> Optional[datetime]:
        if reference_date is None:
            reference_date = datetime.now()
        
        date_str = date_str.lower().strip()
        
        match = re.match(r'^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$', date_str)
        if match:
            day, month, year = match.groups()
            year = int(year)
            if year < 100:
                year += 2000
            try:
                return datetime(year, int(month), int(day))
            except ValueError:
                pass
        
        match = re.match(r'^(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*(?:\s+(\d{2,4}))?$', date_str)
        if match:
            day, month_str, year = match.groups()
            month = self.month_names.get(month_str[:3])
            if month:
                if year:
                    year = int(year)
                    if year < 100:
                        year += 2000
                else:
                    year = reference_date.year
                    if month < reference_date.month:
                        year += 1
                try:
                    return datetime(year, month, int(day))
                except ValueError:
                    pass
        
        day_name = next((name for name in self.day_names if name in date_str), None)
        
        if 'friday' in date_str or 'monday' in date_str or 'tuesday' in date_str or \
           'wednesday' in date_str or 'thursday' in date_str or 'saturday' in date_str or \
           'sunday' in date_str:
            for name, day_num in self.day_names.items():
                if name in date_str:
                    day_name = day_num
                    break
            
            if day_name is not None:
                days_ahead = (day_name - reference_date.weekday()) % 7
                if days_ahead == 0:
                    days_ahead = 7
                return reference_date + timedelta(days=days_ahead)
        
        if 'next week' in date_str:
            days_until_monday = (7 - reference_date.weekday()) % 7
            if days_until_monday == 0:
                days_until_monday = 7
            return reference_date + timedelta(days=days_until_monday)
        
        if 'next month' in date_str:
            if reference_date.month == 12:
                return datetime(reference_date.year + 1, 1, 1)
            else:
                return datetime(reference_date.year, reference_date.month + 1, 1)
        
        match = re.search(r'in\s+(\d+)\s+(?:days|business days|working days)', date_str)
        if match:
            days = int(match.group(1))
            return reference_date + timedelta(days=days)
        
        return None

    def _calculate_confidence(self, text: str, category: str, match_count: int) -> float:
        base_confidence = 0.3
        keyword_boost = min(0.4, match_count * 0.1)
        pattern_boost = 0.1 if self._has_pattern_match(text, category) else 0
        length_factor = min(0.2, len(text) / 2000)
        
        confidence = base_confidence + keyword_boost + pattern_boost + length_factor
        return min(confidence, 0.95)

    def _has_pattern_match(self, text: str, category: str) -> bool:
        patterns = self.patterns[category]
        for pattern in patterns.get('reason_patterns', []):
            if re.search(pattern, text, re.IGNORECASE):
                return True
        for pattern in patterns.get('date_patterns', []):
            if re.search(pattern, text, re.IGNORECASE):
                return True
        for pattern in patterns.get('return_patterns', []):
            if re.search(pattern, text, re.IGNORECASE):
                return True
        for pattern in patterns.get('query_phrases', []):
            if re.search(pattern, text, re.IGNORECASE):
                return True
        for pattern in patterns.get('reference_patterns', []):
            if re.search(pattern, text, re.IGNORECASE):
                return True
        for pattern in patterns.get('unsubscribe_patterns', []):
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    def _extract_data(self, text: str, category: str) -> Dict[str, Any]:
        extracted = {}
        patterns = self.patterns[category]
        
        if category == 'dispute':
            reasons = []
            for pattern in patterns['reason_patterns']:
                matches = re.findall(pattern, text, re.IGNORECASE)
                reasons.extend(matches)
            extracted['reasons'] = list(set(reasons)) if reasons else ['unspecified dispute']
            extracted['dispute_type'] = self._classify_dispute_type(text)
        
        elif category == 'promise_to_pay':
            dates = []
            for pattern in patterns['date_patterns']:
                matches = re.findall(pattern, text, re.IGNORECASE)
                for match in matches:
                    if isinstance(match, tuple):
                        match = match[0] if match else ''
                    parsed_date = self._parse_date(match)
                    if parsed_date:
                        dates.append(parsed_date)
            
            extracted['promised_dates'] = [d.isoformat() for d in dates]
            extracted['payment_amount'] = self._extract_payment_amount(text)
            extracted['payment_method'] = self._extract_payment_method(text)
        
        elif category == 'out_of_office':
            return_dates = []
            for pattern in patterns['return_patterns']:
                matches = re.findall(pattern, text, re.IGNORECASE)
                for match in matches:
                    if isinstance(match, tuple):
                        match = match[0] if match else ''
                    parsed_date = self._parse_date(match)
                    if parsed_date:
                        return_dates.append(parsed_date)
            
            extracted['return_dates'] = [d.isoformat() for d in return_dates]
            extracted['absence_reason'] = self._extract_absence_reason(text)
            extracted['duration_days'] = self._calculate_duration_days(return_dates)
        
        elif category == 'query':
            queries = []
            for pattern in patterns['query_phrases']:
                matches = re.findall(pattern, text, re.IGNORECASE)
                queries.extend(matches)
            extracted['queries'] = list(set(queries))
            extracted['urgency'] = self._assess_urgency(text)
        
        elif category == 'payment_confirmation':
            references = []
            for pattern in patterns['reference_patterns']:
                matches = re.findall(pattern, text, re.IGNORECASE)
                references.extend(matches)
            extracted['references'] = list(set(references))
            extracted['payment_amount'] = self._extract_payment_amount(text)
            extracted['payment_date'] = self._extract_payment_date(text)
        
        elif category == 'unsubscribe':
            unsubscribe_reasons = []
            for pattern in patterns['unsubscribe_patterns']:
                matches = re.findall(pattern, text, re.IGNORECASE)
                unsubscribe_reasons.extend(matches)
            extracted['reasons'] = list(set(unsubscribe_reasons))
            extracted['scope'] = self._determine_unsubscribe_scope(text)
        
        return extracted

    def _classify_dispute_type(self, text: str) -> str:
        text_lower = text.lower()
        if any(phrase in text_lower for phrase in ['already paid', 'paid in full', 'paid last week']):
            return 'payment_already_made'
        elif any(phrase in text_lower for phrase in ['didn\'t receive', 'not received', 'never got']):
            return 'service_not_received'
        elif any(phrase in text_lower for phrase in ['incorrect', 'wrong amount', 'too much', 'overcharged']):
            return 'incorrect_amount'
        elif any(phrase in text_lower for phrase in ['not mine', 'someone else', 'wrong person']):
            return 'not_mine'
        else:
            return 'general'

    def _extract_payment_amount(self, text: str) -> Optional[str]:
        match = re.search(r'\$(\d+(?:,\d{3})*(?:\.\d{2})?)', text)
        if match:
            return match.group(1).replace(',', '')
        return None

    def _extract_payment_method(self, text: str) -> Optional[str]:
        text_lower = text.lower()
        methods = {
            'credit card': ['credit card', 'visa', 'mastercard', 'amex'],
            'bank transfer': ['bank transfer', 'direct debit', 'eft', 'bsb'],
            'cheque': ['cheque', 'check', 'cheque in the mail'],
            'cash': ['cash', 'cash payment'],
            'online': ['online', 'paypal', 'internet banking']
        }
        
        for method, keywords in methods.items():
            if any(keyword in text_lower for keyword in keywords):
                return method
        return None

    def _extract_absence_reason(self, text: str) -> Optional[str]:
        text_lower = text.lower()
        if any(phrase in text_lower for phrase in ['on holiday', 'vacation', 'annual leave']):
            return 'holiday'
        elif any(phrase in text_lower for phrase in ['sick', 'medical', 'health']):
            return 'medical'
        elif any(phrase in text_lower for phrase in ['conference', 'meeting', 'business trip']):
            return 'business'
        return None

    def _calculate_duration_days(self, return_dates: List[datetime]) -> Optional[int]:
        if not return_dates:
            return None
        
        now = datetime.now()
        earliest = min(return_dates)
        return max(1, (earliest - now).days)

    def _assess_urgency(self, text: str) -> str:
        urgent_phrases = ['urgent', 'immediately', 'asap', 'right away', 'deadline']
        high_phrases = ['soon', 'important', 'priority', 'time-sensitive']
        
        text_lower = text.lower()
        if any(phrase in text_lower for phrase in urgent_phrases):
            return 'urgent'
        elif any(phrase in text_lower for phrase in high_phrases):
            return 'high'
        return 'normal'

    def _extract_payment_date(self, text: str) -> Optional[str]:
        date_patterns = [
            r'paid\s+(?:on|on\s+the|dated)\s+(.+?)(?:\.|,|$)',
            r'payment\s+(?:on|dated)\s+(.+?)(?:\.|,|$)',
            r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'(\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*(?:\s+\d{2,4})?)'
        ]
        
        for pattern in date_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                parsed = self._parse_date(match)
                if parsed:
                    return parsed.isoformat()
        return None

    def _determine_unsubscribe_scope(self, text: str) -> str:
        text_lower = text.lower()
        if any(phrase in text_lower for phrase in ['all communications', 'everything', 'any emails']):
            return 'all'
        elif any(phrase in text_lower for phrase in ['marketing only', 'promotional', 'marketing emails']):
            return 'marketing_only'
        return 'all'

    def classify(self, text: str) -> Dict[str, Any]:
        scores = {}
        
        for category, patterns in self.patterns.items():
            keyword_count = 0
            for keyword in patterns['keywords']:
                if keyword.lower() in text.lower():
                    keyword_count += 1
            
            scores[category] = {
                'keyword_count': keyword_count,
                'pattern_match': self._has_pattern_match(text, category)
            }
        
        category_scores = []
        for category, data in scores.items():
            score = data['keyword_count'] * 2 + (1 if data['pattern_match'] else 0)
            category_scores.append((category, score))
        
        category_scores.sort(key=lambda x: x[1], reverse=True)
        
        if category_scores[0][1] == 0:
            category = 'other'
            confidence = 0.1
            reason = 'No matching patterns found'
            extracted_data = {}
        else:
            category = category_scores[0][0]
            match_count = category_scores[0][1]
            confidence = self._calculate_confidence(text, category, match_count)
            reason = f'Found {match_count} matching indicators'
            extracted_data = self._extract_data(text, category)
        
        return {
            'category': category,
            'confidence': round(confidence, 2),
            'reason': reason,
            'extracted_data': extracted_data
        }

    def handle_reply(self, debtor_id: str, text: str, engine: Any) -> Dict[str, Any]:
        classification = self.classify(text)
        category = classification['category']
        
        if category == 'dispute':
            engine.file_dispute(debtor_id, classification['extracted_data'].get('reasons', []))
            engine.pause_escalation(debtor_id)
            engine.notify_owner(debtor_id, f"Dispute received: {', '.join(classification['extracted_data'].get('reasons', []))}")
            
            return {
                'action_taken': 'dispute Filed, escalation paused',
                'notify_owner': True,
                'message': 'Dispute filed and escalation paused. Owner notified.'
            }
        
        elif category == 'promise_to_pay':
            promise_dates = classification['extracted_data'].get('promised_dates', [])
            if promise_dates:
                promise_date = promise_dates[0]
                engine.log_promise(debtor_id, promise_date)
                engine.pause_escalation_until(debtor_id, promise_date)
                
                return {
                    'action_taken': f'Promise to pay logged for {promise_date}',
                    'notify_owner': False,
                    'message': f'Promise logged. Escalation paused until {promise_date}.'
                }
            else:
                return {
                    'action_taken': 'Promise to pay recognized but no date extracted',
                    'notify_owner': True,
                    'message': 'Promise to pay recognized but could not extract date. Manual review needed.'
                }
        
        elif category == 'out_of_office':
            return_dates = classification['extracted_data'].get('return_dates', [])
            if return_dates:
                return_date = return_dates[0]
                engine.pause_escalation_until(debtor_id, return_date)
            else:
                pause_until = (datetime.now() + timedelta(days=14)).isoformat()
                engine.pause_escalation_until(debtor_id, pause_until)
            
            case_number = engine.get_case_number(debtor_id)
            engine.send_acknowledgment(debtor_id, 'out_of_office', case_number=case_number)
            
            return {
                'action_taken': 'Out of office acknowledged, escalation paused',
                'notify_owner': False,
                'message': 'OOO reply processed. Escalation paused and acknowledgment sent.'
            }
        
        elif category == 'query':
            engine.flag_for_review(debtor_id, classification['extracted_data'].get('queries', []))
            engine.notify_owner(debtor_id, f"Query received: {', '.join(classification['extracted_data'].get('queries', [])[:2])}")
            
            return {
                'action_taken': 'Query flagged for manual review',
                'notify_owner': True,
                'message': 'Query flagged. Owner notified for manual review.'
            }
        
        elif category == 'payment_confirmation':
            references = classification['extracted_data'].get('references', [])
            payment_status = engine.verify_payment(debtor_id, references)
            
            if payment_status == 'verified':
                engine.update_account_status(debtor_id, 'paid')
                return {
                    'action_taken': 'Payment verified and account updated',
                    'notify_owner': False,
                    'message': f'Payment verified with reference(s): {", ".join(references)}'
                }
            else:
                engine.flag_for_review(debtor_id, [f"Payment verification needed for: {', '.join(references)}"])
                return {
                    'action_taken': 'Payment verification needed',
                    'notify_owner': True,
                    'message': 'Payment claimed but needs verification. Flagged for review.'
                }
        
        elif category == 'unsubscribe':
            engine.mark_no_contact(debtor_id)
            engine.log_compliance_note(debtor_id, 'Unsubscribe request received and processed')
            
            return {
                'action_taken': 'Marked as no-contact per unsubscribe request',
                'notify_owner': False,
                'message': 'Debtor marked as no-contact. Compliance note logged.'
            }
        
        else:
            return {
                'action_taken': 'No specific action - classified as other',
                'notify_owner': True,
                'message': 'Reply classified as other. Owner notified for manual review.'
            }

    def acknowledgment_template(self, category: str, debtor_name: str, business_name: str) -> str:
        templates = {
            'dispute': f"Dear {debtor_name},\n\nWe've received your dispute regarding your account with {business_name} and have paused collection activities while we review the matter. Our team will contact you within 2 business days to discuss this further.\n\nReference: This communication has been logged for our records.\n\nKind regards,\n{business_name} Collections Team",
            
            'promise_to_pay': f"Dear {debtor_name},\n\nThank you for your response regarding your account with {business_name}. We've noted your payment commitment and will pause all reminders until the agreed payment date.\n\nIf your circumstances change or you need to discuss payment arrangements, please contact us.\n\nKind regards,\n{business_name} Collections Team",
            
            'out_of_office': f"Dear {debtor_name},\n\nWe've received your out-of-office reply and have noted your absence. We will pause all communications until your return.\n\nThis matter will be automatically reviewed upon your return. For urgent matters during your absence, please ensure someone with authority can respond on your behalf.\n\nKind regards,\n{business_name} Collections Team",
            
            'query': f"Dear {debtor_name},\n\nThank you for your query regarding your account with {business_name}. We've logged your questions and a member of our team will respond within 24 business hours.\n\nFor immediate assistance, please contact our customer service team.\n\nKind regards,\n{business_name} Collections Team",
            
            'payment_confirmation': f"Dear {debtor_name},\n\nThank you for confirming your payment regarding your account with {business_name}. We will verify this payment and update our records accordingly.\n\nIf you have any questions about this payment, please don't hesitate to contact us.\n\nKind regards,\n{business_name} Collections Team"
        }
        
        return templates.get(category, f"Dear {debtor_name},\n\nThank you for your response regarding your account with {business_name}. We've received your communication and will respond appropriately.\n\nKind regards,\n{business_name} Collections Team")

    def batch_process(self, replies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results = []
        
        for reply in replies:
            debtor_id = reply.get('debtor_id', 'unknown')
            text = reply.get('text', '')
            received_at = reply.get('received_at', datetime.now().isoformat())
            
            classification = self.classify(text)
            
            result = {
                'debtor_id': debtor_id,
                'received_at': received_at,
                'classification': classification,
                'processed_at': datetime.now().isoformat(),
                'batch_id': f"BATCH_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            }
            results.append(result)
        
        return results