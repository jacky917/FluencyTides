import axios from 'axios';
import {
  AnkiDeckInfo,
  AnkiModelInfo,
  CardGenerateRequest,
  CardGenerateResponse,
  GraphData,
  CardDetail,
  CardRelationCreate,
} from '../types/api';

// Create a configured axios instance
// Vite proxy routes /api to the backend
const apiClient = axios.create({
  baseURL: '/api/v1',
  headers: {
    'Content-Type': 'application/json',
  },
});

// Response interceptor to unwrap data and handle common errors
apiClient.interceptors.response.use(
  (response) => response.data,
  (error) => {
    // Attempt to extract the ErrorResponse from the backend
    if (error.response?.data?.error_code) {
      return Promise.reject(error.response.data);
    }
    return Promise.reject(error);
  }
);

export const FluencyTidesAPI = {
  // Cards
  generateCard: (request: CardGenerateRequest): Promise<CardGenerateResponse> =>
    apiClient.post('/cards/generate', request),

  listModels: (): Promise<AnkiModelInfo[]> => 
    apiClient.get('/cards/models'),

  listDecks: (): Promise<AnkiDeckInfo[]> => 
    apiClient.get('/cards/decks'),

  getKnowledgeGraph: (deckName?: string): Promise<GraphData> =>
    apiClient.get('/relations/graph', { params: deckName && deckName !== 'All Decks' ? { deck_name: deckName } : {} }),

  createRelation: (relation: CardRelationCreate) => 
    apiClient.post('/relations/', relation),

  deleteRelation: (relation: {source_label: string, target_label: string, relation_type: string}): Promise<{ deleted_count: number }> => 
    apiClient.post('/relations/delete', relation),

  getRelationTypes: (): Promise<string[]> => 
    apiClient.get('/relations/types'),
  syncRelations: (): Promise<{ deleted_count: number }> =>
    apiClient.post('/relations/sync'),

  // Phase 6: RUD Operations
  getCard: (noteId: number): Promise<CardDetail> =>
    apiClient.get(`/cards/${noteId}`),

  updateCard: (noteId: number, fields: Record<string, string>): Promise<{ message: string }> =>
    apiClient.put(`/cards/${noteId}`, { fields }),

  deleteCard: (noteId: number): Promise<{ message: string }> =>
    apiClient.delete(`/cards/${noteId}`),

  // Health
  checkHealth: (): Promise<{ status: string }> => 
    axios.get('/api/health').then(res => res.data),
};
